#!/usr/bin/env python3
"""IFS BURCIM_KANTAR_TAB — giriş Kil kapısı, çıkış KNT_KOMHMJ anlık ağırlık."""

from __future__ import annotations

import logging
import os
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "irsaliye_config.json"

# Varsayılanlar — asıl değer irsaliye_config.json / ortam değişkeninden gelir.
EMPTY_KG = 250.0
STABLE_DELTA_KG = 80.0

NO_WEIGHT = "NO_WEIGHT"
UNSTABLE = "UNSTABLE"
STABLE = "STABLE"
COMMUNICATION_ERROR = "COMMUNICATION_ERROR"

log = logging.getLogger("kantar")
_HARDCODED_DEBT_LOGGED = False

# 153 giriş = Kil kapısı (5), 165 çıkış = KNT_KOMHMJ (6)
SCALES = {
    "153": {"kantar_no": 5, "like": "%KIL%KAPI%", "label": "Kil kapısı"},
    "165": {"kantar_no": 6, "like": "%KOMHMJ%", "label": "KNT_KOMHMJ"},
}


def _tns_dirs() -> list[Path]:
    home = Path.home()
    return [
        home / "Desktop" / "kod",
        home / "Documents",
        ROOT,
        Path(r"C:\app\client\SAMETE\product\12.1.0\client_1\network\admin"),
    ]


def _apply_tns() -> None:
    if os.environ.get("TNS_ADMIN"):
        return
    for p in _tns_dirs():
        if (p / "tnsnames.ora").is_file():
            os.environ["TNS_ADMIN"] = str(p)
            return


def empty_threshold() -> float:
    """Kantar boş eşiği (kg). Config: empty_kg, ortam: KANTAR_EMPTY_KG."""
    return float(_cfg()["empty_kg"])


def stable_delta() -> float:
    """Stabil kabul için ardışık okumalar arası max fark (kg)."""
    return float(_cfg()["stable_delta_kg"])


def weight_status(snap: dict | None, seated: bool = False) -> str:
    """Mevcut kilit/okuma sonucunu UI'nin anlayacağı duruma çevir. Yeni algoritma yok."""
    if snap is None:
        return COMMUNICATION_ERROR
    if snap.get("empty") or snap.get("kg") is None:
        return NO_WEIGHT
    if seated or snap.get("seated"):
        return STABLE
    return UNSTABLE


def _ifs_password(data: dict) -> str:
    """Öncelik: ortam > config. Hard-coded fallback teknik borç (bağlantıyı kırmamak için)."""
    global _HARDCODED_DEBT_LOGGED
    env = os.environ.get("IFS_PASSWORD")
    if env:
        return env
    cfg = data.get("ifs_password")
    if cfg:
        return str(cfg)
    if not _HARDCODED_DEBT_LOGGED:
        log.warning(
            "operation=ifs_password error=missing_config "
            "IFS şifresi config/env'de yok; built-in fallback kullanılıyor (teknik borç)"
        )
        _HARDCODED_DEBT_LOGGED = True
    return "burcim2013"


def _cfg() -> dict:
    data: dict = {}
    try:
        import json

        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        log.warning("operation=ifs_config error=%s path=%s", exc, CONFIG_PATH)
        data = {}
    scales = {
        "153": {
            "kantar_no": int(data.get("ifs_kantar_no") or SCALES["153"]["kantar_no"]),
            "like": SCALES["153"]["like"],
            "label": str(data.get("ifs_kantar_aciklama") or SCALES["153"]["label"]),
        },
        "165": {
            "kantar_no": int(data.get("ifs_cikis_kantar_no") or SCALES["165"]["kantar_no"]),
            "like": SCALES["165"]["like"],
            "label": str(data.get("ifs_cikis_aciklama") or SCALES["165"]["label"]),
        },
    }
    empty = data.get("scale_empty_threshold", data.get("empty_kg", os.environ.get("KANTAR_EMPTY_KG", EMPTY_KG)))
    delta = data.get(
        "stable_tolerance_kg",
        data.get("stable_delta_kg", os.environ.get("KANTAR_STABLE_DELTA_KG", STABLE_DELTA_KG)),
    )
    return {
        "dsn": data.get("ifs_dsn") or os.environ.get("IFS_DSN") or "PROD",
        "user": os.environ.get("IFS_USER") or data.get("ifs_user") or "IFSAPP",
        "password": _ifs_password(data),
        "empty_kg": float(empty if empty not in (None, "") else EMPTY_KG),
        "stable_delta_kg": float(delta if delta not in (None, "") else STABLE_DELTA_KG),
        "scale_clear_duration_seconds": float(data.get("scale_clear_duration_seconds") or 2),
        "stable_sample_count": int(data.get("stable_sample_count") or 3),
        "stable_duration_seconds": float(data.get("stable_duration_seconds") or 1.5),
        "ocr_candidate_window_seconds": float(data.get("ocr_candidate_window_seconds") or 20),
        "ocr_candidate_timeout_seconds": float(data.get("ocr_candidate_timeout_seconds") or 45),
        "ocr_min_confirmations": int(data.get("ocr_min_confirmations") or 2),
        "ocr_min_confidence": float(data.get("ocr_min_confidence") or 0),
        "plate_scale_match_window_seconds": float(data.get("plate_scale_match_window_seconds") or 45),
        "stabilization_timeout_seconds": float(data.get("stabilization_timeout_seconds") or 90),
        "vehicle_entering_timeout_seconds": float(data.get("vehicle_entering_timeout_seconds") or 60),
        "waiting_scale_clear_timeout_seconds": float(
            data.get("waiting_scale_clear_timeout_seconds") or 180
        ),
        "scale_debug": bool(data.get("scale_debug") or os.environ.get("PLAKA_SCALE_DEBUG")),
        "scales": scales,
    }


_CONN = None


def _connect():
    global _CONN
    _apply_tns()
    import pyodbc

    cfg = _cfg()
    if _CONN is not None:
        try:
            _CONN.cursor().execute("SELECT 1 FROM DUAL")
            return _CONN
        except Exception as exc:
            log.warning("operation=ifs_ping error=%s", exc)
            try:
                _CONN.close()
            except Exception as close_exc:
                log.warning("operation=ifs_close error=%s", close_exc)
            _CONN = None
    cs = (
        f"Driver={{Oracle in OraClient12Home1}};"
        f"DBQ={cfg['dsn']};UID={cfg['user']};PWD={cfg['password']}"
    )
    _CONN = pyodbc.connect(cs, timeout=8)
    return _CONN


def _row(row) -> dict:
    kg = float(row[2] or 0)
    ts = row[3]
    if isinstance(ts, datetime):
        ts_s = ts.strftime("%H:%M:%S")
        when = ts
    else:
        ts_s = datetime.now().strftime("%H:%M:%S")
        when = datetime.now()
    return {
        "kantar_no": int(row[0] or 0),
        "name": str(row[1] or "").strip(),
        "kg": kg,
        "ts": ts_s,
        "when": when,
        "empty": kg < empty_threshold(),
    }


def live_weight(cam_id: str = "153") -> dict | None:
    """Kameranın kantarına o saniye yazılan SON_AGIRLIK."""
    cfg = _cfg()
    scale = cfg["scales"].get(cam_id) or cfg["scales"]["153"]
    sql = """
        SELECT kantar_no, aciklama, son_agirlik, rowversion
          FROM IFSAPP.BURCIM_KANTAR_TAB
         WHERE kantar_no = ?
            OR UPPER(REPLACE(aciklama, 'I', 'I')) LIKE ?
         ORDER BY CASE WHEN kantar_no = ? THEN 0 ELSE 1 END
    """
    try:
        cur = _connect().cursor()
        cur.execute(sql, (scale["kantar_no"], scale["like"], scale["kantar_no"]))
        row = cur.fetchone()
    except Exception as exc:
        log.error(
            "operation=live_weight camera=%s error=%s",
            cam_id,
            exc,
        )
        return None
    if not row:
        return None
    rec = _row(row)
    rec["cam_id"] = cam_id
    rec["label"] = scale["label"]
    rec["status"] = NO_WEIGHT if rec["empty"] else UNSTABLE
    return rec


def format_kg(kg: float | None) -> str:
    if kg is None:
        return "—"
    return f"{kg:,.0f} kg".replace(",", ".")
