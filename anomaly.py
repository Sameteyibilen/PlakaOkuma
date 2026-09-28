#!/usr/bin/env python3
"""Rule-based anomali tespiti — veriyi değiştirmez, açıklar, gerekiyorsa MANUAL_REVIEW önerir."""

from __future__ import annotations

import json
import logging
import statistics
from datetime import datetime
from pathlib import Path
from typing import Any

from irsaliye import format_plate, issue_dt, plate_key, plates_in_text

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "irsaliye_config.json"
log = logging.getLogger("kantar")

INFO = "INFO"
WARNING = "WARNING"
CRITICAL = "CRITICAL"
ACTIVE = "ACTIVE"
ACKNOWLEDGED = "ACKNOWLEDGED"
RESOLVED = "RESOLVED"

HEALTH_OK = "OK"
HEALTH_WARNING = "WARNING"
HEALTH_CRITICAL = "CRITICAL"

OPEN = frozenset(
    {
        "WAITING_ENTRY_WEIGHT",
        "WAITING_DELIVERY_NOTE",
        "IN_FACILITY",
        "WAITING_EXIT",
        "WAITING_EXIT_WEIGHT",
    }
)

# trigger → bu taramada açılıp kapanabilen kodlar
TRIGGER_CODES = {
    "FULL_WEIGHT_CAPTURED": {
        "DUPLICATE_OPEN_VISIT",
        "RAPID_RETURN",
        "MISSING_PLATE",
        "LOW_OCR_CONFIDENCE",
        "OCR_INCONSISTENCY",
        "SLOW_STABILIZATION",
        "HIGH_WEIGHT_VARIANCE",
        "MISSING_DELIVERY_NOTE",
    },
    "PLATE_DETECTED": {
        "DUPLICATE_OPEN_VISIT",
        "MISSING_PLATE",
        "LOW_OCR_CONFIDENCE",
        "OCR_INCONSISTENCY",
    },
    "IRSALIYE_MATCHED": {
        "MISSING_DELIVERY_NOTE",
        "DELIVERY_NOTE_PLATE_MISMATCH",
        "DELIVERY_NOTE_TIME_MISMATCH",
        "DUPLICATE_DELIVERY_NOTE",
    },
    "EMPTY_WEIGHT_CAPTURED": {
        "UNUSUAL_TARE_WEIGHT",
        "NO_TARE_HISTORY",
        "UNUSUAL_GROSS_WEIGHT",
    },
    "NET_CALCULATED": {
        "INVALID_NET_WEIGHT",
        "NET_WEIGHT_BELOW_MIN",
        "NET_WEIGHT_ABOVE_MAX",
        "UNUSUAL_TARE_WEIGHT",
        "NO_TARE_HISTORY",
        "UNUSUAL_GROSS_WEIGHT",
    },
    "VISIT_COMPLETED": {
        "INVALID_NET_WEIGHT",
        "NET_WEIGHT_BELOW_MIN",
        "NET_WEIGHT_ABOVE_MAX",
        "UNUSUAL_TARE_WEIGHT",
        "NO_TARE_HISTORY",
        "UNUSUAL_GROSS_WEIGHT",
        "DUPLICATE_DELIVERY_NOTE",
        "DELIVERY_NOTE_PLATE_MISMATCH",
        "DELIVERY_NOTE_TIME_MISMATCH",
        "MISSING_DELIVERY_NOTE",
        "DUPLICATE_OPEN_VISIT",
    },
    "TIME_TICK": {
        "LONG_VISIT",
        "MISSING_DELIVERY_NOTE",
    },
    "SCALE_CLEAR_TIMEOUT": {"SCALE_NOT_CLEARED"},
    "SCALE_EMPTY": {"SCALE_ZERO_DRIFT"},
    "COMM_ERROR": {"COMM_ERROR"},
    "OCR_CAPTURE": {
        "LOW_OCR_CONFIDENCE",
        "OCR_INCONSISTENCY",
        "SLOW_STABILIZATION",
        "HIGH_WEIGHT_VARIANCE",
        "MISSING_PLATE",
    },
    "MANUAL_CHANGE": {
        "DELIVERY_NOTE_PLATE_MISMATCH",
        "DELIVERY_NOTE_TIME_MISMATCH",
        "DUPLICATE_DELIVERY_NOTE",
        "MISSING_DELIVERY_NOTE",
        "DUPLICATE_OPEN_VISIT",
        "INVALID_NET_WEIGHT",
        "UNUSUAL_TARE_WEIGHT",
        "NET_WEIGHT_BELOW_MIN",
        "NET_WEIGHT_ABOVE_MAX",
        "MISSING_PLATE",
    },
}

ALWAYS_BLOCK = {
    "INVALID_NET_WEIGHT",
    "DUPLICATE_OPEN_VISIT",
    "MISSING_PLATE",
    "OCR_INCONSISTENCY",
}


def _opt_float(data: dict, key: str) -> float | None:
    raw = data.get(key)
    if raw is None or raw == "":
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _opt_int(data: dict, key: str, default: int | None = None) -> int | None:
    raw = data.get(key)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _opt_bool(data: dict, key: str, default: bool = False) -> bool:
    raw = data.get(key)
    if raw is None or raw == "":
        return default
    if isinstance(raw, bool):
        return raw
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def load_cfg(data: dict | None = None) -> dict:
    raw = data
    if raw is None:
        try:
            raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            raw = {}
        if not isinstance(raw, dict):
            raw = {}
    return {
        "anomaly_enabled": _opt_bool(raw, "anomaly_enabled", True),
        "tare_history_enabled": _opt_bool(raw, "tare_history_enabled", False),
        "tare_history_min_samples": _opt_int(raw, "tare_history_min_samples", 5) or 5,
        "tare_warning_difference_kg": _opt_float(raw, "tare_warning_difference_kg"),
        "tare_critical_difference_kg": _opt_float(raw, "tare_critical_difference_kg"),
        "full_weight_history_enabled": _opt_bool(raw, "full_weight_history_enabled", False),
        "minimum_net_weight": _opt_float(raw, "minimum_net_weight"),
        "maximum_net_weight": _opt_float(raw, "maximum_net_weight"),
        "net_out_of_range_severity": str(raw.get("net_out_of_range_severity") or WARNING).upper(),
        "rapid_return_enabled": _opt_bool(raw, "rapid_return_enabled", False),
        "rapid_return_minutes": _opt_float(raw, "rapid_return_minutes"),
        "long_visit_warning_minutes": _opt_float(raw, "long_visit_warning_minutes"),
        "long_visit_critical_minutes": _opt_float(raw, "long_visit_critical_minutes"),
        "delivery_note_required": _opt_bool(raw, "delivery_note_required", False),
        "delivery_note_missing_warning_minutes": _opt_float(
            raw, "delivery_note_missing_warning_minutes"
        ),
        "missing_delivery_note_severity": str(
            raw.get("missing_delivery_note_severity") or WARNING
        ).upper(),
        "delivery_note_plate_mismatch_enabled": _opt_bool(
            raw, "delivery_note_plate_mismatch_enabled", False
        ),
        "delivery_note_plate_mismatch_severity": str(
            raw.get("delivery_note_plate_mismatch_severity") or WARNING
        ).upper(),
        "delivery_note_time_warning_minutes": _opt_float(
            raw, "delivery_note_time_warning_minutes"
        ),
        "duplicate_delivery_note_check": _opt_bool(raw, "duplicate_delivery_note_check", False),
        "duplicate_delivery_note_severity": str(
            raw.get("duplicate_delivery_note_severity") or WARNING
        ).upper(),
        "ocr_low_confidence_threshold": _opt_float(raw, "ocr_low_confidence_threshold"),
        "slow_stabilization_warning_seconds": _opt_float(
            raw, "slow_stabilization_warning_seconds"
        ),
        "high_weight_variance_kg": _opt_float(raw, "high_weight_variance_kg"),
        "scale_not_cleared_warning_seconds": _opt_float(
            raw, "scale_not_cleared_warning_seconds"
        ),
        "scale_zero_drift_kg": _opt_float(raw, "scale_zero_drift_kg"),
    }


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _parse_ts(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value))
        except (OSError, ValueError, OverflowError):
            return None
    text = str(value).replace("Z", "").strip()
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _minutes(a: datetime | None, b: datetime | None) -> float | None:
    if not a or not b:
        return None
    return abs((b - a).total_seconds()) / 60.0


def _fmt_duration(minutes: float) -> str:
    total = int(round(minutes))
    h, m = divmod(max(0, total), 60)
    if h and m:
        return f"{h} saat {m} dakika"
    if h:
        return f"{h} saat"
    return f"{m} dakika"


def _kg(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def anomaly_key(code: str, visit_id: str, context: str = "") -> str:
    return f"{visit_id}|{code}|{context}"


def _finding(
    *,
    code: str,
    severity: str,
    message: str,
    visit: dict,
    current_value: Any = None,
    reference_value: Any = None,
    difference: Any = None,
    context: str = "",
    extra: dict | None = None,
    auto_block: bool = False,
) -> dict:
    vid = str(visit.get("visit_id") or "")
    rec = {
        "key": anomaly_key(code, vid, context),
        "code": code,
        "severity": severity,
        "message": message,
        "current_value": current_value,
        "reference_value": reference_value,
        "difference": difference,
        "timestamp": _now(),
        "visit_id": vid,
        "plate": visit.get("plate"),
        "status": ACTIVE,
        "acknowledged": False,
        "resolved": False,
        "auto_block": bool(auto_block or (severity == CRITICAL and code in ALWAYS_BLOCK)),
        "context": context,
    }
    if extra:
        rec.update({k: v for k, v in extra.items() if v is not None})
    return rec


def completed_by_plate(visits: list[dict], plate: str, exclude_id: str | None = None) -> list[dict]:
    key = plate_key(plate)
    if not key:
        return []
    rows = [
        v
        for v in visits
        if v.get("plate_key") == key
        and v.get("status") == "COMPLETED"
        and v.get("visit_id") != exclude_id
    ]
    rows.sort(key=lambda v: str(v.get("exit_time") or v.get("updated_at") or ""))
    return rows


def plate_profile(visits: list[dict], plate: str, exclude_id: str | None = None) -> dict:
    done = completed_by_plate(visits, plate, exclude_id)
    tares = [_kg(v.get("empty_weight")) for v in done]
    tares = [x for x in tares if x is not None]
    last = done[-1] if done else None
    return {
        "plate": format_plate(plate) or plate,
        "visit_count": len(done),
        "median_empty": float(statistics.median(tares)) if tares else None,
        "last_empty": tares[-1] if tares else None,
        "min_empty": min(tares) if tares else None,
        "max_empty": max(tares) if tares else None,
        "last_entry": (last or {}).get("entry_time"),
        "last_exit": (last or {}).get("exit_time"),
    }


def health_of(visit: dict) -> str:
    stored = str(visit.get("health") or "")
    active = [
        a
        for a in (visit.get("anomalies") or [])
        if isinstance(a, dict) and a.get("status") == ACTIVE
    ]
    if any(a.get("severity") == CRITICAL for a in active):
        return HEALTH_CRITICAL
    if any(a.get("severity") == WARNING for a in active):
        return HEALTH_WARNING
    if visit.get("status") == "MANUAL_REVIEW":
        return HEALTH_CRITICAL
    return stored if stored in {HEALTH_OK, HEALTH_WARNING, HEALTH_CRITICAL} and not active else HEALTH_OK


def health_label(visit: dict) -> str:
    h = health_of(visit)
    if h == HEALTH_CRITICAL:
        return "🔴 KRİTİK"
    if h == HEALTH_WARNING:
        return "⚠ KONTROL"
    return "✓ NORMAL"


def daily_summary(visits: list[dict], now: datetime | None = None) -> dict:
    day = (now or datetime.now()).date().isoformat()

    def _on_day(visit: dict) -> bool:
        for key in ("entry_time", "exit_time", "created_at", "updated_at"):
            ts = _parse_ts(visit.get(key))
            if ts and ts.date().isoformat() == day:
                return True
        return False

    today = [v for v in visits if _on_day(v)]
    completed = [v for v in today if v.get("status") == "COMPLETED"]
    nets = [_kg(v.get("net_weight")) for v in completed]
    return {
        "vehicles": len(today),
        "completed": len(completed),
        "in_facility": sum(1 for v in visits if v.get("status") in OPEN),
        "review": sum(1 for v in visits if v.get("status") == "MANUAL_REVIEW"),
        "warning": sum(1 for v in today if health_of(v) == HEALTH_WARNING),
        "critical": sum(1 for v in today if health_of(v) == HEALTH_CRITICAL),
        "net_total": round(sum(n for n in nets if n is not None), 1),
    }


def attention_items(visits: list[dict]) -> list[dict]:
    items: list[dict] = []
    for visit in visits:
        for row in visit.get("anomalies") or []:
            if not isinstance(row, dict) or row.get("status") != ACTIVE:
                continue
            if row.get("severity") not in {WARNING, CRITICAL}:
                continue
            items.append(
                {
                    "visit_id": visit.get("visit_id"),
                    "plate": visit.get("plate"),
                    "code": row.get("code"),
                    "severity": row.get("severity"),
                    "message": row.get("message"),
                    "timestamp": row.get("timestamp"),
                }
            )
    rank = {CRITICAL: 0, WARNING: 1}
    items.sort(key=lambda r: (rank.get(str(r.get("severity")), 9), str(r.get("timestamp") or "")))
    return items[:20]


def _irsaliye_doc(irs_id: str) -> dict | None:
    if not irs_id:
        return None
    try:
        from irsaliye import IrsaliyeIndex

        idx = IrsaliyeIndex()
        idx.refresh()
        key = str(irs_id).strip().upper().replace(" ", "")
        for rec in idx.docs:
            if str(rec.get("id") or "").upper().replace(" ", "") == key:
                return rec
    except Exception as exc:
        log.warning("operation=anomaly_irsaliye error=%s id=%s", exc, irs_id)
    return None


def _check_invalid_net(visit: dict) -> dict | None:
    full = _kg(visit.get("full_weight"))
    empty = _kg(visit.get("empty_weight"))
    if full is None or empty is None:
        return None
    if full > empty:
        return None
    net = round(full - empty, 1)
    return _finding(
        code="INVALID_NET_WEIGHT",
        severity=CRITICAL,
        message="Dolu ağırlık boş ağırlıktan küçük veya eşit.",
        visit=visit,
        current_value=net,
        reference_value=full,
        difference=round(empty - full, 1),
        extra={"full_weight": full, "empty_weight": empty},
        auto_block=True,
    )


def _check_net_range(visit: dict, cfg: dict) -> list[dict]:
    net = _kg(visit.get("net_weight"))
    if net is None:
        full, empty = _kg(visit.get("full_weight")), _kg(visit.get("empty_weight"))
        if full is None or empty is None:
            return []
        net = round(full - empty, 1)
    out: list[dict] = []
    sev = cfg["net_out_of_range_severity"] if cfg["net_out_of_range_severity"] in {WARNING, CRITICAL} else WARNING
    lo = cfg["minimum_net_weight"]
    hi = cfg["maximum_net_weight"]
    if lo is not None and net < lo:
        out.append(
            _finding(
                code="NET_WEIGHT_BELOW_MIN",
                severity=sev,
                message="Net ağırlık tanımlanan minimumun altında.",
                visit=visit,
                current_value=net,
                reference_value=lo,
                difference=round(net - lo, 1),
                auto_block=sev == CRITICAL,
            )
        )
    if hi is not None and net > hi:
        out.append(
            _finding(
                code="NET_WEIGHT_ABOVE_MAX",
                severity=CRITICAL if sev == CRITICAL else WARNING,
                message="Olağandışı net ağırlık.",
                visit=visit,
                current_value=net,
                reference_value=hi,
                difference=round(net - hi, 1),
                auto_block=sev == CRITICAL,
            )
        )
    return out


def _check_tare(visit: dict, visits: list[dict], cfg: dict) -> list[dict]:
    empty = _kg(visit.get("empty_weight"))
    if empty is None:
        return []
    hist = completed_by_plate(visits, str(visit.get("plate") or ""), str(visit.get("visit_id")))
    tares = [_kg(v.get("empty_weight")) for v in hist]
    tares = [x for x in tares if x is not None]
    if len(tares) < int(cfg["tare_history_min_samples"]):
        return [
            _finding(
                code="NO_TARE_HISTORY",
                severity=INFO,
                message="Geçmiş tartım verisi bulunmuyor.",
                visit=visit,
                current_value=empty,
                reference_value=len(tares),
                extra={"samples": len(tares)},
            )
        ]
    if not cfg["tare_history_enabled"]:
        return []
    warn_kg = cfg["tare_warning_difference_kg"]
    crit_kg = cfg["tare_critical_difference_kg"]
    if warn_kg is None and crit_kg is None:
        return []
    median = float(statistics.median(tares))
    diff = round(empty - median, 1)
    adiff = abs(diff)
    pct = round((adiff / median) * 100.0, 1) if median else None
    sev = None
    if crit_kg is not None and adiff >= crit_kg:
        sev = CRITICAL
    elif warn_kg is not None and adiff >= warn_kg:
        sev = WARNING
    if not sev:
        return []
    return [
        _finding(
            code="UNUSUAL_TARE_WEIGHT",
            severity=sev,
            message="Bu aracın boş ağırlığı geçmiş tartımlarına göre farklı.",
            visit=visit,
            current_value=empty,
            reference_value=round(median, 1),
            difference=diff,
            extra={"deviation_pct": pct, "samples": len(tares)},
            auto_block=sev == CRITICAL,
        )
    ]


def _check_gross(visit: dict, visits: list[dict], cfg: dict) -> dict | None:
    if not cfg["full_weight_history_enabled"]:
        return None
    full = _kg(visit.get("full_weight"))
    warn_kg = cfg["tare_warning_difference_kg"]
    if full is None or warn_kg is None:
        return None
    hist = completed_by_plate(visits, str(visit.get("plate") or ""), str(visit.get("visit_id")))
    vals = [_kg(v.get("full_weight")) for v in hist]
    vals = [x for x in vals if x is not None]
    if len(vals) < int(cfg["tare_history_min_samples"]):
        return None
    median = float(statistics.median(vals))
    diff = round(full - median, 1)
    if abs(diff) < warn_kg:
        return None
    return _finding(
        code="UNUSUAL_GROSS_WEIGHT",
        severity=INFO,
        message="Dolu ağırlık geçmiş medyandan farklı (yük değişebilir).",
        visit=visit,
        current_value=full,
        reference_value=round(median, 1),
        difference=diff,
        extra={"samples": len(vals)},
    )


def _check_duplicate_open(visit: dict, visits: list[dict]) -> dict | None:
    key = visit.get("plate_key") or plate_key(str(visit.get("plate") or ""))
    if not key:
        return None
    opens = [
        v
        for v in visits
        if v.get("plate_key") == key and v.get("status") in OPEN
    ]
    if visit.get("status") == "MANUAL_REVIEW" and visit.get("manual_review_reason") == "MULTIPLE_OPEN_VISITS":
        siblings = [
            v
            for v in visits
            if v.get("plate_key") == key
            and v.get("manual_review_reason") == "MULTIPLE_OPEN_VISITS"
        ]
        if len(siblings) >= 2:
            opens = siblings
    if len(opens) < 2:
        return None
    ids = [str(v.get("visit_id")) for v in opens]
    return _finding(
        code="DUPLICATE_OPEN_VISIT",
        severity=CRITICAL,
        message="Aynı plakaya ait birden fazla açık işlem var.",
        visit=visit,
        current_value=len(opens),
        extra={"related_visit_ids": ids},
        auto_block=True,
    )


def _check_rapid_return(visit: dict, visits: list[dict], cfg: dict) -> dict | None:
    if not cfg["rapid_return_enabled"] or cfg["rapid_return_minutes"] is None:
        return None
    entry = _parse_ts(visit.get("entry_time"))
    if not entry:
        return None
    prev = completed_by_plate(visits, str(visit.get("plate") or ""), str(visit.get("visit_id")))
    if not prev:
        return None
    last_exit = _parse_ts(prev[-1].get("exit_time") or prev[-1].get("updated_at"))
    lag = _minutes(last_exit, entry)
    if lag is None or lag > float(cfg["rapid_return_minutes"]):
        return None
    return _finding(
        code="RAPID_RETURN",
        severity=WARNING,
        message="Aynı plaka kısa süre içinde yeniden giriş yaptı.",
        visit=visit,
        current_value=round(lag, 1),
        reference_value=cfg["rapid_return_minutes"],
        extra={"previous_visit_id": prev[-1].get("visit_id")},
    )


def _check_long_visit(visit: dict, cfg: dict, now: datetime) -> dict | None:
    if visit.get("status") not in OPEN:
        return None
    entry = _parse_ts(visit.get("entry_time") or visit.get("created_at"))
    warn = cfg["long_visit_warning_minutes"]
    crit = cfg["long_visit_critical_minutes"]
    if not entry or (warn is None and crit is None):
        return None
    mins = _minutes(entry, now)
    if mins is None:
        return None
    sev = None
    if crit is not None and mins >= crit:
        sev = CRITICAL
    elif warn is not None and mins >= warn:
        sev = WARNING
    if not sev:
        return None
    return _finding(
        code="LONG_VISIT",
        severity=sev,
        message=f"Araç uzun süredir tesiste ({_fmt_duration(mins)}).",
        visit=visit,
        current_value=round(mins, 1),
        reference_value=crit if sev == CRITICAL else warn,
        extra={"duration_text": _fmt_duration(mins)},
        auto_block=False,
    )


def _has_irs(visit: dict) -> bool:
    return bool(visit.get("irsaliye_no") or visit.get("irsaliye_id"))


def _check_missing_dn(visit: dict, cfg: dict, now: datetime) -> dict | None:
    if _has_irs(visit):
        return None
    if visit.get("full_weight") is None:
        return None
    status = visit.get("status")
    if status == "COMPLETED" and not cfg["delivery_note_required"]:
        return None
    if status not in OPEN and status not in {"MANUAL_REVIEW", "COMPLETED"}:
        return None
    required = cfg["delivery_note_required"]
    wait_m = cfg["delivery_note_missing_warning_minutes"]
    entry = _parse_ts(visit.get("entry_time") or visit.get("created_at")) or now
    elapsed = _minutes(entry, now) or 0.0
    fire = False
    if required:
        fire = True
    elif wait_m is not None and elapsed >= wait_m:
        fire = True
    if not fire:
        return None
    sev = cfg["missing_delivery_note_severity"]
    if sev not in {WARNING, CRITICAL}:
        sev = WARNING
    return _finding(
        code="MISSING_DELIVERY_NOTE",
        severity=sev,
        message="Giriş tartımı alındı, irsaliye bağlanmadı.",
        visit=visit,
        current_value=round(elapsed, 1),
        extra={"duration_text": _fmt_duration(elapsed)},
        auto_block=sev == CRITICAL and status != "COMPLETED",
    )


def xml_plate_keys(rec: dict | None) -> list[str]:
    """İrsaliye kaydındaki tüm plaka yazımları — 16HS151 / 16 HS 151 fark etmez."""
    if not rec:
        return []
    keys: list[str] = []
    chunks: list[str] = []
    chunks.extend(str(p) for p in (rec.get("plates") or []))
    if rec.get("plate"):
        chunks.append(str(rec.get("plate")))
    chunks.extend(str(n) for n in (rec.get("notes") or []) if n)
    for line in rec.get("lines") or []:
        if isinstance(line, dict):
            chunks.extend(str(line.get(k) or "") for k in ("name", "sku", "id"))
    for raw in chunks:
        found = plates_in_text(raw) or ([plate_key(raw)] if plate_key(raw) else [])
        for key in found:
            if key and key not in keys:
                keys.append(key)
    return keys


def _check_dn_plate(visit: dict, cfg: dict, extra: dict) -> dict | None:
    if not cfg["delivery_note_plate_mismatch_enabled"]:
        return None
    if not _has_irs(visit):
        return None
    ocr_key = plate_key(str(visit.get("plate") or ""))
    if not ocr_key:
        return None
    rec = extra.get("irsaliye_rec") or _irsaliye_doc(
        str(visit.get("irsaliye_id") or visit.get("irsaliye_no") or "")
    )
    if not rec:
        return None
    xml_plates = xml_plate_keys(rec)
    if not xml_plates:
        return None
    if ocr_key in xml_plates:
        return None
    sev = cfg["delivery_note_plate_mismatch_severity"]
    if sev not in {WARNING, CRITICAL}:
        sev = WARNING
    shown = format_plate(xml_plates[0]) or xml_plates[0]
    return _finding(
        code="DELIVERY_NOTE_PLATE_MISMATCH",
        severity=sev,
        message="İrsaliye plakası OCR plakası ile uyuşmuyor.",
        visit=visit,
        current_value=visit.get("plate"),
        reference_value=shown,
        extra={"irsaliye_id": visit.get("irsaliye_id") or visit.get("irsaliye_no")},
        auto_block=sev == CRITICAL,
    )


def _check_dn_time(visit: dict, cfg: dict, extra: dict) -> dict | None:
    limit = cfg["delivery_note_time_warning_minutes"]
    if limit is None or not _has_irs(visit):
        return None
    rec = extra.get("irsaliye_rec") or _irsaliye_doc(str(visit.get("irsaliye_id") or visit.get("irsaliye_no") or ""))
    if not rec:
        return None
    issued = issue_dt(rec)
    when = _parse_ts(visit.get("entry_time") or visit.get("created_at"))
    lag = _minutes(issued, when)
    if lag is None or lag <= limit:
        return None
    return _finding(
        code="DELIVERY_NOTE_TIME_MISMATCH",
        severity=WARNING,
        message="İrsaliye zamanı ile araç zamanı arasında büyük fark var.",
        visit=visit,
        current_value=round(lag, 1),
        reference_value=limit,
        extra={"irsaliye_time": f"{rec.get('date') or ''} {rec.get('time') or ''}".strip()},
    )


def _check_dup_dn(visit: dict, visits: list[dict], cfg: dict) -> dict | None:
    if not cfg["duplicate_delivery_note_check"] or not _has_irs(visit):
        return None
    irs = str(visit.get("irsaliye_no") or visit.get("irsaliye_id") or "").strip().upper()
    if not irs:
        return None
    others = [
        v
        for v in visits
        if v.get("visit_id") != visit.get("visit_id")
        and str(v.get("irsaliye_no") or v.get("irsaliye_id") or "").strip().upper() == irs
    ]
    if not others:
        return None
    sev = cfg["duplicate_delivery_note_severity"]
    if sev not in {WARNING, CRITICAL}:
        sev = WARNING
    ids = [str(v.get("visit_id")) for v in others]
    plates = [str(v.get("plate") or "") for v in others]
    return _finding(
        code="DUPLICATE_DELIVERY_NOTE",
        severity=sev,
        message="Aynı irsaliye birden fazla visit üzerinde kullanılmış.",
        visit=visit,
        current_value=irs,
        extra={"related_visit_ids": ids, "related_plates": plates},
        auto_block=sev == CRITICAL,
    )


def _check_missing_plate(visit: dict) -> dict | None:
    plate = str(visit.get("plate") or "")
    if plate and plate != "TANIMSIZ" and plate_key(plate):
        return None
    return _finding(
        code="MISSING_PLATE",
        severity=CRITICAL,
        message="Plaka tespit edilemedi.",
        visit=visit,
        current_value=plate or "TANIMSIZ",
        auto_block=True,
    )


def _check_ocr_conf(visit: dict, cfg: dict, extra: dict) -> dict | None:
    thr = cfg["ocr_low_confidence_threshold"]
    if thr is None or thr <= 0:
        return None
    conf = extra.get("confidence")
    if conf is None:
        sel = extra.get("selected") or {}
        if isinstance(sel, dict):
            conf = sel.get("avg_conf")
    if conf is None:
        return None
    try:
        conf_f = float(conf)
    except (TypeError, ValueError):
        return None
    if conf_f > 1:
        conf_f = conf_f / 100.0
    if conf_f >= thr:
        return None
    return _finding(
        code="LOW_OCR_CONFIDENCE",
        severity=WARNING,
        message="Plaka okuması kontrol edilmeli.",
        visit=visit,
        current_value=round(conf_f, 3),
        reference_value=thr,
        extra={"plate": visit.get("plate")},
    )


def _check_ocr_inconsistency(visit: dict, extra: dict) -> dict | None:
    cands = extra.get("candidates") or visit.get("pending_candidates") or []
    rows = [c for c in cands if isinstance(c, dict) and c.get("plate")]
    if len(rows) < 2:
        return None
    grouped: dict[str, int] = {}
    for rec in rows:
        grouped[plate_key(str(rec.get("plate")))] = grouped.get(
            plate_key(str(rec.get("plate"))), 0
        ) + int(rec.get("count") or 1)
    grouped.pop("", None)
    if len(grouped) < 2:
        return None
    counts = sorted(grouped.values(), reverse=True)
    if counts[0] >= counts[1] * 2 and counts[0] >= 3:
        return None
    return _finding(
        code="OCR_INCONSISTENCY",
        severity=CRITICAL,
        message="Aynı cycle içinde tutarsız plaka okumaları var.",
        visit=visit,
        current_value=len(grouped),
        extra={"candidates": [{"plate": k, "count": n} for k, n in grouped.items()]},
        auto_block=True,
    )


def _check_slow_stab(visit: dict, cfg: dict, extra: dict) -> dict | None:
    limit = cfg["slow_stabilization_warning_seconds"]
    if limit is None:
        return None
    sec = extra.get("stabilize_seconds")
    if sec is None:
        return None
    try:
        sec_f = float(sec)
    except (TypeError, ValueError):
        return None
    if sec_f < limit:
        return None
    return _finding(
        code="SLOW_STABILIZATION",
        severity=WARNING,
        message="Ağırlık normalden uzun sürede oturdu.",
        visit=visit,
        current_value=round(sec_f, 1),
        reference_value=limit,
    )


def _check_variance(visit: dict, cfg: dict, extra: dict) -> dict | None:
    limit = cfg["high_weight_variance_kg"]
    if limit is None:
        return None
    samples = extra.get("samples") or []
    nums = [_kg(x) for x in samples]
    nums = [x for x in nums if x is not None]
    if len(nums) < 3:
        return None
    span = max(nums) - min(nums)
    if span < limit:
        return None
    return _finding(
        code="HIGH_WEIGHT_VARIANCE",
        severity=INFO,
        message="Ağırlık ölçümleri normalden fazla değişkenlik gösterdi.",
        visit=visit,
        current_value=round(span, 1),
        reference_value=limit,
    )


def _check_scale_not_cleared(visit: dict, cfg: dict, extra: dict) -> dict | None:
    limit = cfg["scale_not_cleared_warning_seconds"]
    if limit is None:
        return None
    waited = extra.get("waited_seconds")
    if waited is None:
        return None
    try:
        sec = float(waited)
    except (TypeError, ValueError):
        return None
    if sec < limit:
        return None
    return _finding(
        code="SCALE_NOT_CLEARED",
        severity=WARNING,
        message="Araç kantardan ayrılmadı.",
        visit=visit,
        current_value=round(sec, 1),
        reference_value=limit,
    )


def _check_zero_drift(visit: dict, cfg: dict, extra: dict) -> dict | None:
    limit = cfg["scale_zero_drift_kg"]
    kg = _kg(extra.get("kg"))
    if limit is None or kg is None:
        return None
    if abs(kg) < limit:
        return None
    return _finding(
        code="SCALE_ZERO_DRIFT",
        severity=WARNING,
        message="Kantar boş değerinde sapma olabilir.",
        visit=visit,
        current_value=kg,
        reference_value=0,
        difference=kg,
    )


def _collect(visit: dict, visits: list[dict], trigger: str, cfg: dict, extra: dict, now: datetime) -> list[dict]:
    codes = TRIGGER_CODES.get(trigger) or set()
    found: list[dict] = []

    def add(item: dict | None) -> None:
        if item and item.get("code") in codes:
            found.append(item)

    if "INVALID_NET_WEIGHT" in codes:
        add(_check_invalid_net(visit))
    if {"NET_WEIGHT_BELOW_MIN", "NET_WEIGHT_ABOVE_MAX"} & codes:
        for row in _check_net_range(visit, cfg):
            add(row)
    if {"UNUSUAL_TARE_WEIGHT", "NO_TARE_HISTORY"} & codes:
        for row in _check_tare(visit, visits, cfg):
            add(row)
    if "UNUSUAL_GROSS_WEIGHT" in codes:
        add(_check_gross(visit, visits, cfg))
    if "DUPLICATE_OPEN_VISIT" in codes:
        add(_check_duplicate_open(visit, visits))
    if "RAPID_RETURN" in codes:
        add(_check_rapid_return(visit, visits, cfg))
    if "LONG_VISIT" in codes:
        add(_check_long_visit(visit, cfg, now))
    if "MISSING_DELIVERY_NOTE" in codes:
        add(_check_missing_dn(visit, cfg, now))
    if "DELIVERY_NOTE_PLATE_MISMATCH" in codes:
        add(_check_dn_plate(visit, cfg, extra))
    if "DELIVERY_NOTE_TIME_MISMATCH" in codes:
        add(_check_dn_time(visit, cfg, extra))
    if "DUPLICATE_DELIVERY_NOTE" in codes:
        add(_check_dup_dn(visit, visits, cfg))
    if "MISSING_PLATE" in codes:
        add(_check_missing_plate(visit))
    if "LOW_OCR_CONFIDENCE" in codes:
        add(_check_ocr_conf(visit, cfg, extra))
    if "OCR_INCONSISTENCY" in codes:
        add(_check_ocr_inconsistency(visit, extra))
    if "SLOW_STABILIZATION" in codes:
        add(_check_slow_stab(visit, cfg, extra))
    if "HIGH_WEIGHT_VARIANCE" in codes:
        add(_check_variance(visit, cfg, extra))
    if "SCALE_NOT_CLEARED" in codes:
        add(_check_scale_not_cleared(visit, cfg, extra))
    if "SCALE_ZERO_DRIFT" in codes:
        add(_check_zero_drift(visit, cfg, extra))
    if "COMM_ERROR" in codes:
        add(
            _finding(
                code="COMM_ERROR",
                severity=CRITICAL,
                message="Kantar bağlantısı kesildi.",
                visit=visit,
                current_value=extra.get("kg"),
                auto_block=False,
            )
        )
    return found


def _upsert(visit: dict, finding: dict) -> str:
    """new | updated | unchanged"""
    rows = visit.setdefault("anomalies", [])
    key = finding["key"]
    for row in rows:
        if row.get("key") != key:
            continue
        status = row.get("status")
        if status == RESOLVED:
            finding["status"] = ACTIVE
            finding["resolved"] = False
            finding["acknowledged"] = False
            finding["reopened_at"] = _now()
            row.clear()
            row.update(finding)
            return "new"
        if status == ACKNOWLEDGED:
            for field in ("current_value", "reference_value", "difference", "message", "extra", "deviation_pct", "samples"):
                if field in finding:
                    row[field] = finding[field]
            row["updated_at"] = _now()
            return "updated"
        changed = False
        for field in ("current_value", "reference_value", "difference", "message", "severity"):
            if row.get(field) != finding.get(field):
                row[field] = finding.get(field)
                changed = True
        for field in ("deviation_pct", "samples", "related_visit_ids", "related_plates", "duration_text"):
            if finding.get(field) is not None:
                row[field] = finding[field]
        if changed:
            row["updated_at"] = _now()
            return "updated"
        return "unchanged"
    rows.append(finding)
    return "new"


def _resolve_absent(visit: dict, scanned: set[str], present: set[str]) -> None:
    for row in visit.get("anomalies") or []:
        if not isinstance(row, dict):
            continue
        code = str(row.get("code") or "")
        if code not in scanned or row.get("status") != ACTIVE:
            continue
        if code in present:
            continue
        row["status"] = RESOLVED
        row["resolved"] = True
        row["resolved_at"] = _now()


def _refresh_health(visit: dict) -> str:
    visit["health"] = health_of(visit)
    return visit["health"]


def apply_to_visit(
    visit: dict,
    visits: list[dict],
    trigger: str,
    extra: dict | None = None,
    cfg: dict | None = None,
    now: datetime | None = None,
) -> dict:
    """Visit üzerindeki anomaly listesini günceller. Plaka/kilo/irsaliye değiştirmez."""
    settings = cfg or load_cfg()
    extra = extra or {}
    now = now or datetime.now()
    result = {"new": [], "blocked": False, "health": HEALTH_OK}
    if not settings["anomaly_enabled"] or not visit:
        visit["health"] = health_of(visit)
        result["health"] = visit["health"]
        return result
    found = _collect(visit, visits, trigger, settings, extra, now)
    scanned = TRIGGER_CODES.get(trigger) or set()
    present = {str(f.get("code")) for f in found}
    _resolve_absent(visit, scanned, present)
    for finding in found:
        kind = _upsert(visit, finding)
        if kind == "new":
            result["new"].append(finding)
            visit.setdefault("events", []).append(
                {
                    "ts": _now(),
                    "action": "ANOMALY_DETECTED",
                    "detail": {
                        "code": finding.get("code"),
                        "severity": finding.get("severity"),
                        "message": finding.get("message"),
                    },
                }
            )
            log.info(
                "operation=anomaly_detected visit_id=%s plate=%s code=%s severity=%s",
                visit.get("visit_id"),
                visit.get("plate"),
                finding.get("code"),
                finding.get("severity"),
            )
    health = _refresh_health(visit)
    result["health"] = health
    blockers = [
        f
        for f in found
        if f.get("auto_block") and f.get("severity") == CRITICAL
    ]
    if blockers and visit.get("status") != "MANUAL_REVIEW":
        reason = str(blockers[0].get("code"))
        visit["status"] = "MANUAL_REVIEW"
        visit["manual_review_reason"] = visit.get("manual_review_reason") or reason
        visit["gate"] = "MANUAL_REVIEW"
        visit["wait_reason"] = reason
        result["blocked"] = True
        log.warning(
            "operation=anomaly_block visit_id=%s plate=%s reason=%s",
            visit.get("visit_id"),
            visit.get("plate"),
            reason,
        )
    else:
        _refresh_health(visit)
    return result


def extra_from_cycle(timeline: list | None, candidates: list | None = None) -> dict:
    extra: dict[str, Any] = {}
    if candidates:
        extra["candidates"] = candidates
        confs = []
        for rec in candidates:
            if isinstance(rec, dict) and rec.get("avg_conf") is not None:
                confs.append(float(rec["avg_conf"]))
        if confs:
            extra["confidence"] = max(confs)
            extra["selected"] = max(
                (c for c in candidates if isinstance(c, dict)),
                key=lambda c: float(c.get("score") or 0),
                default=None,
            )
    enter = None
    stable = None
    samples: list[float] = []
    for ev in timeline or []:
        if not isinstance(ev, dict):
            continue
        action = str(ev.get("action") or "")
        ts = ev.get("ts")
        detail = ev.get("detail") or {}
        kg = _kg(detail.get("kg"))
        if kg is not None:
            samples.append(kg)
        if action == "VEHICLE_ENTERING" and enter is None:
            enter = ts
        if action in {"WEIGHT_STABLE", "WEIGHT_CAPTURED"} and stable is None:
            stable = ts
    extra["samples"] = samples
    if isinstance(enter, (int, float)) and isinstance(stable, (int, float)):
        extra["stabilize_seconds"] = max(0.0, float(stable) - float(enter))
    return extra


def acknowledge(visit: dict, code: str, reason: str = "", operator: str = "OPERATOR") -> dict | None:
    for row in visit.get("anomalies") or []:
        if row.get("code") == code and row.get("status") == ACTIVE:
            row["status"] = ACKNOWLEDGED
            row["acknowledged"] = True
            row["acknowledged_at"] = _now()
            row["acknowledged_by"] = operator
            row["ack_reason"] = reason
            visit.setdefault("events", []).append(
                {
                    "ts": _now(),
                    "action": "ANOMALY_ACKNOWLEDGED",
                    "detail": {"code": code, "reason": reason, "operator": operator},
                }
            )
            _refresh_health(visit)
            return row
    return None
