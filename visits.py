#!/usr/bin/env python3
"""Yerel Visit/Transaction store — JSON kalıcılık, ileride SQLite'a taşınabilir."""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import threading
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable

from irsaliye import format_plate, plate_extends, plate_key

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
VISITS_PATH = DATA_DIR / "visits.json"
AUDIT_PATH = DATA_DIR / "audit.json"
LOG_PATH = DATA_DIR / "kantar.log"

# Açık ziyaret — 165 bunları eşler. MANUAL_REVIEW / COMPLETED açık sayılmaz.
OPEN_STATUSES = frozenset(
    {
        "WAITING_ENTRY_WEIGHT",
        "WAITING_DELIVERY_NOTE",
        "IN_FACILITY",
        "WAITING_EXIT",
        "WAITING_EXIT_WEIGHT",
    }
)

REVIEW_OPEN_NOT_FOUND = "OPEN_VISIT_NOT_FOUND"
REVIEW_MULTIPLE_OPEN = "MULTIPLE_OPEN_VISITS"
REVIEW_INVALID_NET = "INVALID_NET_WEIGHT"
REVIEW_MISSING_FULL = "MISSING_FULL_WEIGHT"
REVIEW_PLATE_NOT_DETECTED = "PLATE_NOT_DETECTED"
REVIEW_AMBIGUOUS_PLATE = "AMBIGUOUS_PLATE"
REVIEW_RECOVERY = "RECOVERY_OCCUPIED"
REVIEW_LOW_CONFIDENCE = "LOW_CONFIDENCE"
REVIEW_STAB_TIMEOUT = "STABILIZATION_TIMEOUT"


class VisitStatus:
    WAITING_ENTRY_WEIGHT = "WAITING_ENTRY_WEIGHT"
    WAITING_DELIVERY_NOTE = "WAITING_DELIVERY_NOTE"
    IN_FACILITY = "IN_FACILITY"
    WAITING_EXIT = "WAITING_EXIT"
    WAITING_EXIT_WEIGHT = "WAITING_EXIT_WEIGHT"
    MATCHED = "MATCHED"
    COMPLETED = "COMPLETED"
    MANUAL_REVIEW = "MANUAL_REVIEW"
    ERROR = "ERROR"


class GateState:
    WAIT = "WAIT"
    READY_TO_PASS = "READY_TO_PASS"
    MANUAL_REVIEW = "MANUAL_REVIEW"
    COMPLETED = "COMPLETED"


class AuditAction:
    PLATE_DETECTED = "PLATE_DETECTED"
    WEIGHT_STABLE = "WEIGHT_STABLE"
    FULL_WEIGHT_CAPTURED = "FULL_WEIGHT_CAPTURED"
    EMPTY_WEIGHT_CAPTURED = "EMPTY_WEIGHT_CAPTURED"
    NET_CALCULATED = "NET_CALCULATED"
    IRSALIYE_MATCHED = "IRSALIYE_MATCHED"
    IRSALIYE_CHANGED = "IRSALIYE_CHANGED"
    PLATE_CHANGED = "PLATE_CHANGED"
    READY_TO_PASS = "READY_TO_PASS"
    EXIT_DETECTED = "EXIT_DETECTED"
    COMPLETED = "COMPLETED"
    MANUAL_REVIEW = "MANUAL_REVIEW"
    VISIT_REUSED = "VISIT_REUSED"
    MANUAL_VISIT_MATCH = "MANUAL_VISIT_MATCH"
    ANOMALY_DETECTED = "ANOMALY_DETECTED"
    ANOMALY_ACKNOWLEDGED = "ANOMALY_ACKNOWLEDGED"


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _setup_log() -> logging.Logger:
    log = logging.getLogger("kantar")
    if log.handlers:
        return log
    log.setLevel(logging.INFO)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter(
        "%(asctime)s %(levelname)s module=%(name)s %(message)s"
    )
    fh = logging.FileHandler(LOG_PATH, encoding="utf-8")
    fh.setFormatter(fmt)
    log.addHandler(fh)
    log.propagate = False
    return log


log = _setup_log()


class FileLock:
    """Süreçler arası kilit — Windows msvcrt / POSIX fcntl."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._fp = None

    def __enter__(self) -> FileLock:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fp = open(self.path, "a+b")
        if os.name == "nt":
            import msvcrt

            self._fp.seek(0)
            msvcrt.locking(self._fp.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(self._fp.fileno(), fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc: object) -> None:
        if self._fp is None:
            return
        try:
            if os.name == "nt":
                import msvcrt

                self._fp.seek(0)
                msvcrt.locking(self._fp.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._fp.fileno(), fcntl.LOCK_UN)
        finally:
            self._fp.close()
            self._fp = None


def _atomic_write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


class JsonVisitRepository:
    """Kalıcılık. SQLite'a geçince bu sınıfın yerine başka repository konur."""

    def __init__(
        self,
        visits_path: Path | None = None,
        audit_path: Path | None = None,
    ) -> None:
        self.visits_path = visits_path or VISITS_PATH
        self.audit_path = audit_path or AUDIT_PATH
        self.lock_path = self.visits_path.with_name(self.visits_path.name + ".lock")
        self.bak_path = self.visits_path.with_name(self.visits_path.name + ".bak")
        self._thread = threading.RLock()

    def load_visits(self) -> list[dict]:
        with self._thread, FileLock(self.lock_path):
            return self._read_visits_unlocked()

    def save_visits(self, visits: list[dict]) -> None:
        with self._thread, FileLock(self.lock_path):
            self._write_visits_unlocked(visits)

    def mutate(
        self, fn: Callable[[list[dict]], Any]
    ) -> Any:
        """Tek kilit altında oku-değiştir-yaz. Yarım kayıt oluşmaz."""
        with self._thread, FileLock(self.lock_path):
            visits = self._read_visits_unlocked()
            result = fn(visits)
            self._write_visits_unlocked(visits)
            return result

    def append_audit(self, rec: dict) -> None:
        with self._thread, FileLock(self.lock_path):
            rows = self._read_audit_unlocked()
            rows.append(rec)
            try:
                _atomic_write(self.audit_path, rows)
            except OSError as exc:
                log.error(
                    "operation=audit_write error=%s path=%s",
                    exc,
                    self.audit_path,
                )
                raise

    def _empty_doc(self) -> dict:
        return {"version": 1, "updated_at": _now(), "visits": []}

    def _parse_visits(self, raw: Any) -> list[dict] | None:
        if isinstance(raw, list):
            return [v for v in raw if isinstance(v, dict)]
        if isinstance(raw, dict) and isinstance(raw.get("visits"), list):
            return [v for v in raw["visits"] if isinstance(v, dict)]
        return None

    def _read_visits_unlocked(self) -> list[dict]:
        path = self.visits_path
        if not path.exists():
            return []
        try:
            parsed = self._parse_visits(_load_json(path))
            if parsed is not None:
                return parsed
            raise ValueError("visits.json beklenen şemada değil")
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            log.error(
                "operation=visits_load error=%s path=%s — bozuk dosya [] ile ezilmeyecek",
                exc,
                path,
            )
            recovered = self._recover_unlocked()
            if recovered is not None:
                return recovered
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            quarantine = path.with_name(f"{path.name}.corrupt-{stamp}")
            try:
                shutil.copy2(path, quarantine)
                log.error(
                    "operation=visits_quarantine path=%s",
                    quarantine,
                )
            except OSError as copy_exc:
                log.error(
                    "operation=visits_quarantine error=%s",
                    copy_exc,
                )
            return []

    def _recover_unlocked(self) -> list[dict] | None:
        bak = self.bak_path
        if not bak.exists():
            return None
        try:
            parsed = self._parse_visits(_load_json(bak))
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            log.error("operation=visits_bak_load error=%s path=%s", exc, bak)
            return None
        if parsed is None:
            return None
        log.warning("operation=visits_recover source=%s count=%s", bak, len(parsed))
        try:
            self._write_visits_unlocked(parsed)
        except OSError as exc:
            log.error("operation=visits_recover_write error=%s", exc)
        return parsed

    def _write_visits_unlocked(self, visits: list[dict]) -> None:
        payload = {
            "version": 1,
            "updated_at": _now(),
            "visits": visits,
        }
        current = self.visits_path
        if current.exists():
            try:
                if self._parse_visits(_load_json(current)) is not None:
                    shutil.copy2(current, self.bak_path)
            except (OSError, json.JSONDecodeError, ValueError):
                log.warning(
                    "operation=visits_backup_skip path=%s — mevcut dosya yedeklenemedi",
                    current,
                )
        try:
            _atomic_write(current, payload)
        except OSError as exc:
            log.error("operation=visits_write error=%s path=%s", exc, current)
            raise

    def _read_audit_unlocked(self) -> list[dict]:
        path = self.audit_path
        if not path.exists():
            return []
        try:
            raw = _load_json(path)
        except (OSError, json.JSONDecodeError) as exc:
            log.error("operation=audit_load error=%s path=%s", exc, path)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            try:
                shutil.copy2(path, path.with_name(f"{path.name}.corrupt-{stamp}"))
            except OSError:
                pass
            return []
        if isinstance(raw, list):
            return raw
        return []


def _new_id() -> str:
    return f"V-{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}"


def _event(action: str, **detail: Any) -> dict:
    rec = {"ts": _now(), "action": action}
    if detail:
        rec["detail"] = {k: v for k, v in detail.items() if v is not None}
    return rec


def compute_gate(visit: dict) -> str:
    status = visit.get("status")
    if status == VisitStatus.MANUAL_REVIEW:
        return GateState.MANUAL_REVIEW
    if status == VisitStatus.COMPLETED:
        return GateState.COMPLETED
    if (
        visit.get("plate")
        and visit.get("full_weight") not in (None, "")
        and (visit.get("irsaliye_no") or visit.get("irsaliye_id"))
    ):
        return GateState.READY_TO_PASS
    return GateState.WAIT


def wait_reason(visit: dict) -> str:
    """UI alt metni için kod — karar UI'da üretilmez."""
    status = visit.get("status")
    if status == VisitStatus.MANUAL_REVIEW:
        return str(visit.get("manual_review_reason") or "MANUAL_REVIEW")
    if status == VisitStatus.COMPLETED:
        return "COMPLETED"
    if compute_gate(visit) == GateState.READY_TO_PASS:
        return "READY_TO_PASS"
    if not visit.get("plate"):
        return "WAITING_PLATE"
    if visit.get("full_weight") is None:
        return "WAITING_WEIGHT"
    if visit.get("status") == VisitStatus.WAITING_EXIT_WEIGHT:
        return "WAITING_WEIGHT"
    if not (visit.get("irsaliye_no") or visit.get("irsaliye_id")):
        return "WAITING_IRSALIYE"
    return "WAITING"


def is_valid_plate(text: str) -> bool:
    key = plate_key(text)
    return bool(key) and bool(re.fullmatch(r"\d{2}[A-Z]{1,3}\d{2,4}", key))


def _refresh(visit: dict) -> dict:
    visit["updated_at"] = _now()
    visit["gate"] = compute_gate(visit)
    visit["wait_reason"] = wait_reason(visit)
    if visit["gate"] == GateState.READY_TO_PASS:
        evs = visit.get("events") or []
        if not any(e.get("action") == AuditAction.READY_TO_PASS for e in evs):
            visit.setdefault("events", []).append(_event(AuditAction.READY_TO_PASS))
    return visit


def _new_visit(
    *,
    plate: str,
    raw_plate: str = "",
    status: str = VisitStatus.IN_FACILITY,
) -> dict:
    formatted = format_plate(plate) or (plate or "").strip().upper()
    return {
        "visit_id": _new_id(),
        "plate": formatted,
        "plate_key": plate_key(formatted),
        "raw_plate": (raw_plate or plate or "").strip(),
        "status": status,
        "gate": GateState.WAIT,
        "entry_camera": None,
        "entry_time": None,
        "full_weight": None,
        "exit_camera": None,
        "exit_time": None,
        "empty_weight": None,
        "net_weight": None,
        "irsaliye_id": None,
        "irsaliye_no": None,
        "irsaliye_source": None,
        "created_at": _now(),
        "updated_at": _now(),
        "manual_review_reason": None,
        "pending_exit": None,
        "events": [],
        "anomalies": [],
        "health": "OK",
    }


def visit_day(visit: dict) -> date | None:
    for key in ("entry_time", "exit_time", "created_at", "updated_at"):
        raw = str(visit.get(key) or "")[:10]
        if not raw:
            continue
        try:
            return date.fromisoformat(raw)
        except ValueError:
            continue
    return None


def is_today_visit(visit: dict, day: date | None = None) -> bool:
    day = day or date.today()
    return visit_day(visit) == day


def _open_today(visits: list[dict]) -> list[dict]:
    return [
        v
        for v in visits
        if v.get("status") in OPEN_STATUSES and is_today_visit(v) and v.get("plate_key")
    ]


def _open_for_plate(visits: list[dict], key: str) -> list[dict]:
    """Tam plaka; yoksa tek uzantı (16BLZ93 → 16BLZ931). Birden fazla uzantı belirsiz kalır."""
    today = _open_today(visits)
    exact = [v for v in today if v.get("plate_key") == key]
    if exact:
        return exact
    if not key or len(key) < 5:
        return []
    longer = [v for v in today if plate_extends(key, str(v.get("plate_key") or ""))]
    if len(longer) == 1:
        return longer
    if len(longer) > 1:
        return longer
    shorter = [v for v in today if plate_extends(str(v.get("plate_key") or ""), key)]
    if len(shorter) == 1:
        return shorter
    return []


def _find(visits: list[dict], visit_id: str) -> dict | None:
    for v in visits:
        if v.get("visit_id") == visit_id:
            return v
    return None


def _mark_review(visit: dict, reason: str, **detail: Any) -> None:
    visit["status"] = VisitStatus.MANUAL_REVIEW
    visit["manual_review_reason"] = reason
    visit["events"].append(_event(AuditAction.MANUAL_REVIEW, reason=reason, **detail))
    _refresh(visit)


def _set_full(visit: dict, kg: float | None, camera: str) -> bool:
    if kg is None:
        return False
    visit["full_weight"] = float(kg)
    visit["entry_camera"] = camera
    if not visit.get("entry_time"):
        visit["entry_time"] = _now()
    visit["events"].append(
        _event(AuditAction.FULL_WEIGHT_CAPTURED, kg=float(kg), camera=camera)
    )
    if visit["status"] == VisitStatus.WAITING_ENTRY_WEIGHT:
        visit["status"] = (
            VisitStatus.WAITING_DELIVERY_NOTE
            if not (visit.get("irsaliye_no") or visit.get("irsaliye_id"))
            else VisitStatus.IN_FACILITY
        )
    elif visit["status"] in OPEN_STATUSES and visit["status"] != VisitStatus.WAITING_EXIT_WEIGHT:
        if visit.get("irsaliye_no") or visit.get("irsaliye_id"):
            visit["status"] = VisitStatus.IN_FACILITY
        else:
            visit["status"] = VisitStatus.WAITING_DELIVERY_NOTE
    _refresh(visit)
    return True


def _complete_exit(visit: dict, kg: float | None, camera: str) -> str:
    """empty_weight yazar; net hesaplar. Dönen: COMPLETED / MANUAL_REVIEW / WAITING_EXIT_WEIGHT."""
    visit["exit_camera"] = camera
    if not visit.get("exit_time"):
        visit["exit_time"] = _now()
    if kg is None:
        visit["status"] = VisitStatus.WAITING_EXIT_WEIGHT
        _refresh(visit)
        return VisitStatus.WAITING_EXIT_WEIGHT
    visit["empty_weight"] = float(kg)
    visit["events"].append(
        _event(AuditAction.EMPTY_WEIGHT_CAPTURED, kg=float(kg), camera=camera)
    )
    full = visit.get("full_weight")
    if full is None:
        _mark_review(visit, REVIEW_MISSING_FULL, empty_weight=float(kg))
        return VisitStatus.MANUAL_REVIEW
    net = round(float(full) - float(kg), 1)
    visit["net_weight"] = net
    visit["events"].append(
        _event(AuditAction.NET_CALCULATED, full_weight=float(full), empty_weight=float(kg), net_weight=net)
    )
    if net <= 0:
        _mark_review(visit, REVIEW_INVALID_NET, net_weight=net)
        return VisitStatus.MANUAL_REVIEW
    visit["status"] = VisitStatus.COMPLETED
    visit["manual_review_reason"] = None
    visit["events"].append(_event(AuditAction.COMPLETED, net_weight=net))
    _refresh(visit)
    return VisitStatus.COMPLETED


class VisitStore:
    """Giriş/çıkış eşleme kuralları. UI ve OCR aynı store'u kullanır."""

    def __init__(self, repo: JsonVisitRepository | None = None) -> None:
        self.repo = repo or JsonVisitRepository()
        self._anomaly_tick_at = 0.0

    def _apply_anomalies(
        self,
        visit: dict | None,
        visits: list[dict],
        trigger: str,
        extra: dict | None = None,
    ) -> dict | None:
        if not visit:
            return visit
        from anomaly import apply_to_visit

        apply_to_visit(visit, visits, trigger, extra=extra or {})
        _refresh(visit)
        return visit

    def all_visits(self) -> list[dict]:
        return [v for v in self.repo.load_visits() if is_today_visit(v)]

    def prune_old_visits(self, day: date | None = None) -> int:
        keep = day or date.today()

        def _op(visits: list[dict]) -> int:
            kept = [v for v in visits if is_today_visit(v, keep)]
            removed = len(visits) - len(kept)
            if removed:
                visits[:] = kept
            return removed

        return self.repo.mutate(_op)

    def get(self, visit_id: str) -> dict | None:
        return _find(self.repo.load_visits(), visit_id)

    def open_visits(self, plate: str) -> list[dict]:
        key = plate_key(plate)
        return _open_for_plate(self.repo.load_visits(), key)

    def on_entry(
        self,
        *,
        plate: str,
        raw_plate: str = "",
        camera: str = "153",
        full_weight: float | None = None,
        confidence: float | None = None,
    ) -> dict:
        """153: açık kayıt varsa yenisini açma; yoksa dolu tartımlı visit oluştur."""

        def _op(visits: list[dict]) -> dict:
            key = plate_key(plate)
            formatted = format_plate(plate) or plate
            opens = _open_for_plate(visits, key)
            if len(opens) > 1:
                pending = {
                    "camera": camera,
                    "time": _now(),
                    "full_weight": full_weight,
                }
                for v in opens:
                    if full_weight is not None and v.get("full_weight") is None:
                        _set_full(v, full_weight, camera)
                    v["pending_entry"] = pending
                    _mark_review(v, REVIEW_MULTIPLE_OPEN, camera=camera)
                log.warning(
                    "operation=on_entry plate=%s camera=%s error=MULTIPLE_OPEN_VISITS count=%s",
                    formatted,
                    camera,
                    len(opens),
                )
                self._audit_unlocked(
                    action=AuditAction.MANUAL_REVIEW,
                    visit_id=opens[0].get("visit_id"),
                    plate=formatted,
                    old_value=None,
                    new_value=REVIEW_MULTIPLE_OPEN,
                    source="SYSTEM",
                    camera=camera,
                )
                self._apply_anomalies(opens[0], visits, "FULL_WEIGHT_CAPTURED")
                return {
                    "action": "manual_review",
                    "reason": REVIEW_MULTIPLE_OPEN,
                    "visit": opens[0],
                    "open_count": len(opens),
                }
            if len(opens) == 1:
                visit = opens[0]
                visit["events"].append(
                    _event(
                        AuditAction.VISIT_REUSED,
                        camera=camera,
                        plate=formatted,
                        confidence=confidence,
                    )
                )
                if full_weight is not None and visit.get("full_weight") is None:
                    _set_full(visit, full_weight, camera)
                else:
                    _refresh(visit)
                log.info(
                    "operation=on_entry action=reuse visit_id=%s plate=%s camera=%s",
                    visit.get("visit_id"),
                    formatted,
                    camera,
                )
                trigger = "FULL_WEIGHT_CAPTURED" if visit.get("full_weight") is not None else "PLATE_DETECTED"
                self._apply_anomalies(visit, visits, trigger, extra={"confidence": confidence})
                return {"action": "reuse", "visit": visit, "open_count": 1}

            visit = _new_visit(plate=formatted, raw_plate=raw_plate or plate)
            visit["entry_camera"] = camera
            visit["entry_time"] = _now()
            visit["events"].append(
                _event(
                    AuditAction.PLATE_DETECTED,
                    camera=camera,
                    plate=formatted,
                    raw_plate=raw_plate,
                    confidence=confidence,
                    direction="entry",
                )
            )
            if full_weight is not None:
                _set_full(visit, full_weight, camera)
            else:
                visit["status"] = VisitStatus.WAITING_ENTRY_WEIGHT
                _refresh(visit)
            visits.append(visit)
            log.info(
                "operation=on_entry action=create visit_id=%s plate=%s camera=%s full_weight=%s status=%s",
                visit["visit_id"],
                formatted,
                camera,
                visit.get("full_weight"),
                visit.get("status"),
            )
            trigger = "FULL_WEIGHT_CAPTURED" if full_weight is not None else "PLATE_DETECTED"
            self._apply_anomalies(visit, visits, trigger, extra={"confidence": confidence})
            return {"action": "create", "visit": visit, "open_count": 0}

        result = self.repo.mutate(_op)
        return result

    def on_exit(
        self,
        *,
        plate: str,
        raw_plate: str = "",
        camera: str = "165",
        empty_weight: float | None = None,
        confidence: float | None = None,
    ) -> dict:
        """165: tek açık kayıt varsa boş+net; aksi halde MANUAL_REVIEW."""

        def _op(visits: list[dict]) -> dict:
            key = plate_key(plate)
            formatted = format_plate(plate) or plate
            opens = _open_for_plate(visits, key)
            if len(opens) > 1:
                pending = {
                    "camera": camera,
                    "time": _now(),
                    "empty_weight": empty_weight,
                    "raw_plate": raw_plate,
                }
                for v in opens:
                    v["pending_exit"] = pending
                    _mark_review(v, REVIEW_MULTIPLE_OPEN, camera=camera, empty_weight=empty_weight)
                log.warning(
                    "operation=on_exit plate=%s camera=%s error=MULTIPLE_OPEN_VISITS count=%s",
                    formatted,
                    camera,
                    len(opens),
                )
                self._audit_unlocked(
                    action=AuditAction.MANUAL_REVIEW,
                    visit_id=opens[0].get("visit_id"),
                    plate=formatted,
                    old_value=None,
                    new_value=REVIEW_MULTIPLE_OPEN,
                    source="SYSTEM",
                    camera=camera,
                )
                self._apply_anomalies(opens[0], visits, "NET_CALCULATED")
                return {
                    "action": "manual_review",
                    "reason": REVIEW_MULTIPLE_OPEN,
                    "visit": opens[0],
                    "open_count": len(opens),
                }
            if not opens:
                visit = _new_visit(
                    plate=formatted,
                    raw_plate=raw_plate or plate,
                    status=VisitStatus.MANUAL_REVIEW,
                )
                visit["exit_camera"] = camera
                visit["exit_time"] = _now()
                visit["manual_review_reason"] = REVIEW_OPEN_NOT_FOUND
                visit["events"].append(
                    _event(
                        AuditAction.EXIT_DETECTED,
                        camera=camera,
                        plate=formatted,
                        confidence=confidence,
                    )
                )
                if empty_weight is not None:
                    visit["empty_weight"] = float(empty_weight)
                    visit["events"].append(
                        _event(
                            AuditAction.EMPTY_WEIGHT_CAPTURED,
                            kg=float(empty_weight),
                            camera=camera,
                        )
                    )
                visit["events"].append(
                    _event(AuditAction.MANUAL_REVIEW, reason=REVIEW_OPEN_NOT_FOUND)
                )
                _refresh(visit)
                visits.append(visit)
                log.warning(
                    "operation=on_exit visit_id=%s plate=%s camera=%s error=OPEN_VISIT_NOT_FOUND empty_weight=%s",
                    visit["visit_id"],
                    formatted,
                    camera,
                    visit.get("empty_weight"),
                )
                self._audit_unlocked(
                    action=AuditAction.MANUAL_REVIEW,
                    visit_id=visit["visit_id"],
                    plate=formatted,
                    old_value=None,
                    new_value=REVIEW_OPEN_NOT_FOUND,
                    source="SYSTEM",
                    camera=camera,
                )
                self._apply_anomalies(
                    visit, visits, "NET_CALCULATED", extra={"confidence": confidence}
                )
                return {
                    "action": "manual_review",
                    "reason": REVIEW_OPEN_NOT_FOUND,
                    "visit": visit,
                    "open_count": 0,
                }

            visit = opens[0]
            visit["events"].append(
                _event(
                    AuditAction.EXIT_DETECTED,
                    camera=camera,
                    plate=formatted,
                    confidence=confidence,
                )
            )
            status = _complete_exit(visit, empty_weight, camera)
            log.info(
                "operation=on_exit action=match visit_id=%s plate=%s camera=%s empty_weight=%s net_weight=%s status=%s",
                visit.get("visit_id"),
                formatted,
                camera,
                visit.get("empty_weight"),
                visit.get("net_weight"),
                status,
            )
            if status == VisitStatus.MANUAL_REVIEW:
                self._audit_unlocked(
                    action=AuditAction.MANUAL_REVIEW,
                    visit_id=visit.get("visit_id"),
                    plate=formatted,
                    old_value=None,
                    new_value=visit.get("manual_review_reason"),
                    source="SYSTEM",
                    camera=camera,
                )
                self._apply_anomalies(
                    visit, visits, "NET_CALCULATED", extra={"confidence": confidence}
                )
                return {
                    "action": "manual_review",
                    "reason": visit.get("manual_review_reason"),
                    "visit": visit,
                    "open_count": 1,
                }
            trigger = "VISIT_COMPLETED" if status == VisitStatus.COMPLETED else "NET_CALCULATED"
            self._apply_anomalies(visit, visits, trigger, extra={"confidence": confidence})
            return {
                "action": "complete" if status == VisitStatus.COMPLETED else "waiting_weight",
                "visit": visit,
                "open_count": 1,
            }

        return self.repo.mutate(_op)

    def apply_stable_weight(
        self,
        *,
        plate: str,
        camera: str,
        kg: float,
    ) -> dict | None:
        """OCR plakayı ağırlıktan önce okuduysa oturmuş kiloyu açık visit'e yaz."""

        def _op(visits: list[dict]) -> dict | None:
            key = plate_key(plate)
            opens = _open_for_plate(visits, key)
            if len(opens) != 1:
                return None
            visit = opens[0]
            if camera == "153":
                if visit.get("full_weight") is not None:
                    return visit
                visit["events"].append(_event(AuditAction.WEIGHT_STABLE, kg=float(kg), camera=camera))
                _set_full(visit, kg, camera)
                log.info(
                    "operation=apply_stable_weight visit_id=%s plate=%s camera=%s full_weight=%s",
                    visit.get("visit_id"),
                    visit.get("plate"),
                    camera,
                    kg,
                )
                return visit
            if camera == "165":
                if visit.get("empty_weight") is not None:
                    return visit
                visit["events"].append(_event(AuditAction.WEIGHT_STABLE, kg=float(kg), camera=camera))
                _complete_exit(visit, kg, camera)
                log.info(
                    "operation=apply_stable_weight visit_id=%s plate=%s camera=%s empty_weight=%s net_weight=%s status=%s",
                    visit.get("visit_id"),
                    visit.get("plate"),
                    camera,
                    visit.get("empty_weight"),
                    visit.get("net_weight"),
                    visit.get("status"),
                )
                return visit
            return visit

        return self.repo.mutate(_op)

    def attach_irsaliye(
        self,
        *,
        visit_id: str | None = None,
        plate: str | None = None,
        irsaliye_id: str = "",
        irsaliye_no: str = "",
        irsaliye_source: str = "",
        source: str = "OPERATOR",
    ) -> dict | None:
        """Mevcut Al/Kaydet için — bir sonraki UI aşamasında bağlanacak."""

        def _op(visits: list[dict]) -> dict | None:
            visit = _find(visits, visit_id) if visit_id else None
            if visit is None and plate:
                opens = _open_for_plate(visits, plate_key(plate))
                if len(opens) == 1:
                    visit = opens[0]
                elif not opens:
                    # son ziyaret (tamamlanmış dahil)
                    key = plate_key(plate)
                    matches = [v for v in visits if v.get("plate_key") == key]
                    visit = matches[-1] if matches else None
            if visit is None:
                log.warning(
                    "operation=attach_irsaliye error=visit_not_found visit_id=%s plate=%s",
                    visit_id,
                    plate,
                )
                return None
            old = visit.get("irsaliye_no") or visit.get("irsaliye_id")
            new = (irsaliye_no or irsaliye_id or "").strip()
            visit["irsaliye_id"] = (irsaliye_id or new).strip() or None
            visit["irsaliye_no"] = new or None
            visit["irsaliye_source"] = (irsaliye_source or "").strip() or None
            action = AuditAction.IRSALIYE_CHANGED if old else AuditAction.IRSALIYE_MATCHED
            visit["events"].append(_event(action, old_value=old, new_value=new))
            if visit["status"] == VisitStatus.WAITING_DELIVERY_NOTE and visit.get("full_weight") is not None:
                visit["status"] = VisitStatus.IN_FACILITY
            _refresh(visit)
            self._audit_unlocked(
                action=action,
                visit_id=visit.get("visit_id"),
                plate=visit.get("plate"),
                old_value=old,
                new_value=new,
                source=source,
            )
            extra = {"irsaliye_id": new}
            self._apply_anomalies(visit, visits, "IRSALIYE_MATCHED", extra=extra)
            return visit

        return self.repo.mutate(_op)

    def change_plate(
        self,
        *,
        visit_id: str,
        new_plate: str,
        source: str = "OPERATOR",
        reason: str = "",
        changed_by: str = "",
    ) -> dict:
        """Plakayı güncelle. Çakışmada merge yok; kayıt değişmez."""

        def _op(visits: list[dict]) -> dict:
            visit = _find(visits, visit_id)
            if visit is None:
                return {"ok": False, "error": "NOT_FOUND", "visit": None}
            if not (new_plate or "").strip() or not is_valid_plate(new_plate):
                return {"ok": False, "error": "INVALID_PLATE", "visit": visit}
            formatted = format_plate(new_plate) or new_plate.strip().upper()
            new_key = plate_key(formatted)
            conflicts = [
                v
                for v in visits
                if v.get("visit_id") != visit_id
                and v.get("plate_key") == new_key
                and v.get("status") in OPEN_STATUSES
            ]
            if conflicts:
                log.warning(
                    "operation=change_plate visit_id=%s error=PLATE_CONFLICT plate=%s",
                    visit_id,
                    formatted,
                )
                return {
                    "ok": False,
                    "error": "PLATE_CONFLICT",
                    "visit": visit,
                    "conflicts": conflicts,
                }
            old = visit.get("plate")
            visit["plate"] = formatted
            visit["plate_key"] = new_key
            visit["events"].append(
                _event(AuditAction.PLATE_CHANGED, old_value=old, new_value=formatted, reason=reason)
            )
            _refresh(visit)
            self._audit_unlocked(
                action=AuditAction.PLATE_CHANGED,
                visit_id=visit_id,
                plate=formatted,
                old_value=old,
                new_value=formatted,
                source=source,
                extra={
                    "reason": reason,
                    "changed_by": changed_by,
                    "weigh_cycle_id": visit.get("weigh_cycle_id"),
                },
            )
            self._apply_anomalies(visit, visits, "MANUAL_CHANGE")
            return {"ok": True, "visit": visit, "old_value": old, "new_value": formatted}

        return self.repo.mutate(_op)

    def resolve_multiple_exit(
        self,
        *,
        chosen_id: str,
        source: str = "OPERATOR",
    ) -> dict | None:
        """MULTIPLE_OPEN_VISITS: operatörün seçtiği kayda çıkış tartımını bağla."""

        def _op(visits: list[dict]) -> dict | None:
            chosen = _find(visits, chosen_id)
            if chosen is None:
                return None
            key = chosen.get("plate_key")
            pending = chosen.get("pending_exit") or {}
            if not pending:
                for v in visits:
                    if v.get("plate_key") == key and v.get("pending_exit"):
                        pending = v.get("pending_exit") or {}
                        break
            kg = pending.get("empty_weight")
            camera = str(pending.get("camera") or chosen.get("exit_camera") or "165")
            siblings = [
                v
                for v in visits
                if v.get("plate_key") == key
                and v.get("visit_id") != chosen_id
                and v.get("manual_review_reason") == REVIEW_MULTIPLE_OPEN
            ]
            _complete_exit(chosen, kg if kg is not None else None, camera)
            chosen["pending_exit"] = None
            chosen["events"].append(
                _event(AuditAction.MANUAL_VISIT_MATCH, chosen_id=chosen_id, source=source)
            )
            for sib in siblings:
                sib["pending_exit"] = None
                sib["manual_review_reason"] = None
                if sib.get("full_weight") is not None:
                    sib["status"] = (
                        VisitStatus.IN_FACILITY
                        if (sib.get("irsaliye_no") or sib.get("irsaliye_id"))
                        else VisitStatus.WAITING_DELIVERY_NOTE
                    )
                else:
                    sib["status"] = VisitStatus.WAITING_ENTRY_WEIGHT
                _refresh(sib)
            self._audit_unlocked(
                action=AuditAction.MANUAL_VISIT_MATCH,
                visit_id=chosen_id,
                plate=chosen.get("plate"),
                old_value=REVIEW_MULTIPLE_OPEN,
                new_value=chosen.get("status"),
                source=source,
                camera=camera,
            )
            self._apply_anomalies(chosen, visits, "MANUAL_CHANGE")
            return chosen

        return self.repo.mutate(_op)

    def attach_exit_to_open(
        self,
        *,
        review_id: str,
        open_id: str,
        source: str = "OPERATOR",
    ) -> dict | None:
        """OPEN_VISIT_NOT_FOUND: çıkış tartımını operatörün seçtiği açık kayda taşı."""

        def _op(visits: list[dict]) -> dict | None:
            review = _find(visits, review_id)
            target = _find(visits, open_id)
            if review is None or target is None:
                return None
            kg = review.get("empty_weight")
            if kg is None:
                pending = review.get("pending_exit") or {}
                kg = pending.get("empty_weight")
            camera = str(review.get("exit_camera") or "165")
            _complete_exit(target, kg if kg is not None else None, camera)
            target["events"].append(
                _event(
                    AuditAction.MANUAL_VISIT_MATCH,
                    review_id=review_id,
                    open_id=open_id,
                    source=source,
                )
            )
            review["status"] = VisitStatus.ERROR
            review["linked_visit_id"] = open_id
            review["events"].append(
                _event(AuditAction.MANUAL_VISIT_MATCH, linked_to=open_id, source=source)
            )
            _refresh(review)
            self._audit_unlocked(
                action=AuditAction.MANUAL_VISIT_MATCH,
                visit_id=open_id,
                plate=target.get("plate"),
                old_value=review_id,
                new_value=open_id,
                source=source,
                camera=camera,
            )
            self._apply_anomalies(target, visits, "MANUAL_CHANGE")
            return target

        return self.repo.mutate(_op)

    def all_open(self) -> list[dict]:
        return [
            v
            for v in self.repo.load_visits()
            if v.get("status") in OPEN_STATUSES and is_today_visit(v)
        ]

    def open_plate_keys(self) -> set[str]:
        return {str(v.get("plate_key") or "") for v in self.all_open() if v.get("plate_key")}

    def tag_cycle(
        self,
        visit_id: str,
        cycle_id: str,
        timeline: list[dict] | None = None,
    ) -> dict | None:
        def _op(visits: list[dict]) -> dict | None:
            visit = _find(visits, visit_id)
            if visit is None:
                return None
            visit["weigh_cycle_id"] = cycle_id
            keep = {
                "PLATE_CONFIRMED",
                "WEIGHT_CAPTURED",
                "PLATE_NOT_DETECTED",
                "AMBIGUOUS_PLATE",
            }
            for ev in timeline or []:
                action = str(ev.get("action") or "")
                if action in keep:
                    visit.setdefault("events", []).append(
                        _event(action, **(ev.get("detail") or {}))
                    )
            _refresh(visit)
            from anomaly import extra_from_cycle

            self._apply_anomalies(
                visit,
                visits,
                "OCR_CAPTURE",
                extra=extra_from_cycle(timeline, visit.get("pending_candidates")),
            )
            return visit

        return self.repo.mutate(_op)

    def capture_unidentified(
        self,
        *,
        camera: str,
        kg: float,
        cycle_id: str,
        reason: str,
        plate: str | None = None,
        raw_plate: str = "",
        candidates: list | None = None,
        timeline: list | None = None,
    ) -> dict:
        def _op(visits: list[dict]) -> dict:
            label = format_plate(plate) if plate else "TANIMSIZ"
            if plate and not is_valid_plate(plate):
                label = "TANIMSIZ"
            visit = _new_visit(plate=label if label != "TANIMSIZ" else "TANIMSIZ", raw_plate=raw_plate or plate or "")
            if label == "TANIMSIZ":
                visit["plate"] = "TANIMSIZ"
                visit["plate_key"] = ""
            visit["weigh_cycle_id"] = cycle_id
            _mark_review(visit, reason, kg=kg, camera=camera)
            if camera == "165":
                visit["exit_camera"] = camera
                visit["exit_time"] = _now()
                visit["empty_weight"] = float(kg)
            else:
                visit["entry_camera"] = camera
                visit["entry_time"] = _now()
                visit["full_weight"] = float(kg)
            if candidates:
                visit["pending_candidates"] = [
                    {k: c.get(k) for k in ("plate", "count", "score", "avg_conf", "camera")}
                    for c in candidates[:6]
                    if isinstance(c, dict)
                ]
            keep = {
                "PLATE_CONFIRMED",
                "WEIGHT_CAPTURED",
                "PLATE_NOT_DETECTED",
                "AMBIGUOUS_PLATE",
            }
            for ev in timeline or []:
                action = str(ev.get("action") or "")
                if action in keep:
                    visit.setdefault("events", []).append(_event(action, **(ev.get("detail") or {})))
            _refresh(visit)
            visits.append(visit)
            log.warning(
                "operation=capture_unidentified visit_id=%s camera=%s reason=%s kg=%s cycle=%s",
                visit["visit_id"],
                camera,
                reason,
                kg,
                cycle_id,
            )
            self._audit_unlocked(
                action=AuditAction.MANUAL_REVIEW,
                visit_id=visit["visit_id"],
                plate=visit.get("plate"),
                old_value=None,
                new_value=reason,
                source="SYSTEM",
                camera=camera,
            )
            from anomaly import extra_from_cycle

            self._apply_anomalies(
                visit,
                visits,
                "OCR_CAPTURE",
                extra=extra_from_cycle(timeline, candidates),
            )
            return visit

        return self.repo.mutate(_op)

    def review_siblings(self, plate: str) -> list[dict]:
        key = plate_key(plate)
        return [
            v
            for v in self.repo.load_visits()
            if v.get("plate_key") == key
            and v.get("manual_review_reason") == REVIEW_MULTIPLE_OPEN
        ]

    def _audit_unlocked(
        self,
        *,
        action: str,
        visit_id: str | None,
        plate: str | None,
        old_value: Any,
        new_value: Any,
        source: str,
        camera: str | None = None,
        extra: dict | None = None,
    ) -> None:
        rec = {
            "ts": _now(),
            "action": action,
            "visit_id": visit_id,
            "plate": plate,
            "old_value": old_value,
            "new_value": new_value,
            "source": source,
            "camera": camera,
        }
        if extra:
            rec.update({k: v for k, v in extra.items() if v})
        try:
            rows = self.repo._read_audit_unlocked()
            rows.append(rec)
            _atomic_write(self.repo.audit_path, rows)
        except OSError as exc:
            log.error(
                "operation=audit_write visit_id=%s plate=%s error=%s",
                visit_id,
                plate,
                exc,
            )


_STORE: VisitStore | None = None
_STORE_LOCK = threading.Lock()


def get_store() -> VisitStore:
    global _STORE
    with _STORE_LOCK:
        if _STORE is None:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            _STORE = VisitStore()
            try:
                n = _STORE.prune_old_visits()
                if n:
                    log.info("operation=prune_old_visits removed=%s", n)
            except Exception as exc:
                log.warning("operation=prune_old_visits error=%s", exc)
        return _STORE
