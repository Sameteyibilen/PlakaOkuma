#!/usr/bin/env python3
"""Canlı plaka okuma — hızlı kare yakala, sadece araç varken OCR."""

from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

from eportal import load_config as load_eportal_config
from eportal import sync as sync_eportal
from irsaliye import (
    IrsaliyeIndex,
    complete_open_prefix,
    format_plate,
    plate_extends,
    plate_key,
)
from kantar_ifs import COMMUNICATION_ERROR, format_kg, live_weight, weight_status
from scale_auto import ScaleState, ScaleYard
from visits import get_store

ROOT = Path(__file__).resolve().parent
OUT_DIR = ROOT / "live_ocr"
MODELS_DIR = ROOT / ".models"
OUT_DIR.mkdir(parents=True, exist_ok=True)
(ROOT / ".tmp").mkdir(parents=True, exist_ok=True)
os.environ.setdefault("TEMP", str(ROOT / ".tmp"))
os.environ.setdefault("TMP", str(ROOT / ".tmp"))
os.environ.setdefault("HF_HOME", str(ROOT / ".hf"))

USER, PASS = "admin", "admin"
VALID_CITY = {f"{i:02d}" for i in range(1, 82)}

# hold=True: Dahua. ip varsa kare cihaz HTTP (snapshot.cgi) — RTSP tamponu yok.
# hold=False: Neutron kare al, kapat. 152/164 kullanilmaz.
CAMERAS = [
    {
        "id": "153",
        "name": "Giris",
        "role": "ocr",
        "ocr_id": "153",
        "ip": "172.16.21.153",
        "watch": f"rtsp://{USER}:{PASS}@172.16.21.153:554/cam/realmonitor?channel=1&subtype=1",
        "ocr": f"rtsp://{USER}:{PASS}@172.16.21.153:554/cam/realmonitor?channel=1&subtype=0",
        "hold": True,
        "deck": (0.22, 0.95, 0.08, 0.86),
    },
    {
        "id": "165",
        "name": "Cikis",
        "role": "ocr",
        "ocr_id": "165",
        "watch": f"rtsp://{USER}:{PASS}@172.16.21.165:554/media/video1",
        "ocr": f"rtsp://{USER}:{PASS}@172.16.21.165:554/media/video1",
        "hold": False,
        "deck": (0.22, 0.95, 0.12, 0.88),
    },
]

# Aynı plakayı araç gidene kadar tekrar yazma
DEDUP_SEC = 120
EMPTY_CLEAR_ROUNDS = 5

CAM_TR = {
    "153": "Giriş",
    "165": "Çıkış",
}


def cam_tr(cid: str) -> str:
    return CAM_TR.get(cid, cid)


def _same_visit(a: str, b: str) -> bool:
    """Aynı tam plaka (boşluksuz). Eksik okuma tam olanı kilitlemesin."""
    aa, bb = a.replace(" ", ""), b.replace(" ", "")
    return bool(aa) and aa == bb


def _is_extension(short: str, longer: str) -> bool:
    """34FV675 → 34FV6754 gibi eksik okumanın uzaması."""
    a, b = short.replace(" ", ""), longer.replace(" ", "")
    return bool(a) and bool(b) and b.startswith(a) and len(b) > len(a)


def _status_summary(cams: list[dict]) -> str:
    warmup = [c for c in cams if c["state"] == "warmup"]
    vehicles = [c for c in cams if c["state"] == "vehicle"]
    down = [c for c in cams if c["state"] in {"down", "busy"}]
    if warmup:
        names = ", ".join(cam_tr(c["id"]) for c in warmup)
        n = warmup[0].get("n", 0)
        return f"Boş zemin öğreniliyor ({n}/{BG_WARMUP}) — {names}"
    if vehicles:
        return "Araç var: " + ", ".join(cam_tr(c["id"]) for c in vehicles)
    if down:
        return "Görüntü yok: " + ", ".join(cam_tr(c["id"]) for c in down)
    return "Kantarda araç yok"


def clean_token(text: str) -> str:
    t = text.upper().replace("İ", "I").replace("|", "I").replace("Ö", "O")
    return re.sub(r"[^A-Z0-9]", "", t)


def _plate_from_token(t: str) -> str | None:
    m = re.fullmatch(r"(\d{2})([A-Z]{1,3})(\d{2,4})", t)
    if m and m.group(1) in VALID_CITY:
        return f"{m.group(1)} {m.group(2)} {m.group(3)}"
    return None


_DIGIT_LOOKALIKE = str.maketrans(
    {
        "O": "0",
        "Q": "0",
        "D": "0",
        "I": "1",
        "L": "1",
        "Z": "2",
        "A": "4",
        "S": "5",
        "G": "6",
        "T": "7",
        "B": "8",
    }
)


def _repair_numeric_tail(t: str) -> str | None:
    """34FV67S / 34FV675A → rakam kuyruğundaki S/A karışıklığını düzelt."""
    m = re.match(r"^(\d{2})([A-Z]{1,3})([0-9A-Z]{2,4})$", t)
    if not m:
        return None
    tail = m.group(3).translate(_DIGIT_LOOKALIKE)
    if not tail.isdigit():
        return None
    return _plate_from_token(m.group(1) + m.group(2) + tail)


def normalize_plate(text: str) -> str | None:
    t = clean_token(text)
    hit = _plate_from_token(t)
    if hit:
        return hit
    hit = _repair_numeric_tail(t)
    if hit:
        return hit
    # Mavi TR seridi başa 1 veya 7 ekler; 16EE628 gibi asıl metin içeride
    if t[:1].isdigit() and len(t) >= 7:
        hit = _plate_from_token(t[1:])
        if hit:
            return hit
        hit = _repair_numeric_tail(t[1:])
        if hit:
            return hit
    if t[-1:] in {"1", "3"} and len(t) >= 8:
        hit = _plate_from_token(t[:-1])
        if hit:
            return hit
        if t[:1].isdigit():
            hit = _plate_from_token(t[1:-1])
            if hit:
                return hit
    m1 = re.match(r"^0([A-Z]{1,3})(\d{2,4})$", t)
    if m1:
        return _plate_from_token("10" + t[1:])
    return None


def _plate_variants(plate: str | None) -> list[str]:
    """3 harf + 4 hane TR kalıbı değil; fazla rakamı ayrıca dene. 2+4 kesilmez."""
    if not plate:
        return []
    out = [plate]
    city, letters, nums = plate.split()
    if len(letters) == 3 and len(nums) == 4:
        alt = _plate_from_token(city + letters + nums[:3])
        if alt:
            out.append(alt)
    return out


def is_junk_text(text: str) -> bool:
    """Kamera saati, tarih, marka yazisi — plaka degil."""
    t = text.strip().upper()
    if not t:
        return True
    if t in {"IPC", "TR", "DAF", "FH", "VOLVO", "RENAULT", "MAN", "SCANIA"}:
        return True
    if re.fullmatch(r"[RPGK]\d{2,3}", ct := clean_token(t)):
        return True
    if re.match(r"^20\d{2}", t):
        return True
    if re.match(r"^\d{1,2}[:.]\d{2}([:.]\d{2})?$", t):
        return True
    ct = clean_token(t)
    if re.fullmatch(r"\d{6}", ct):
        hh, mm, ss = int(ct[:2]), int(ct[2:4]), int(ct[4:6])
        if hh <= 23 and mm <= 59 and ss <= 59:
            return True
    return False


def is_clock_plate(plate: str) -> bool:
    """17:00:36 -> 17 O 036 gibi saat okumalarini at."""
    city, letters, nums = plate.split()
    if letters in {"O", "I"} and len(nums) == 3 and nums.startswith("0"):
        return True
    try:
        hh = int(city)
        mm = int(nums[:2]) if len(nums) >= 2 else 99
        if letters in {"O", "I"} and hh <= 23 and mm <= 59:
            return True
    except ValueError:
        pass
    return False


def plate_quality(plate: str, conf: float) -> float:
    _c, letters, nums = plate.split()
    q = conf
    n_l, n_n = len(letters), len(nums)
    if n_l == 2 and n_n == 4:
        q += 0.55
    elif n_l == 3 and n_n == 3:
        q += 0.45
    elif n_l == 2 and n_n == 3:
        q += 0.22
    elif n_l == 3 and n_n == 2:
        q += 0.30
    elif n_l == 1 and n_n == 4:
        q += 0.28
    elif n_l == 3 and n_n == 4:
        q += 0.04
    elif n_n == 2:
        q += 0.02
    if letters in {"O", "I"}:
        q -= 0.6
    if n_l == 1 and n_n < 4:
        q -= 0.4
    if _looks_truncated(plate):
        q -= 0.12
    return q


def is_weak_plate(plate: str, quality: float) -> bool:
    """20 L 20 / 27 DD 111 gibi far-ızgara çöpünü plaka sayma."""
    _c, letters, nums = plate.split()
    if quality < 0.50:
        return True
    if len(letters) + len(nums) < 4:
        return True
    if len(letters) == 1 and len(nums) <= 2:
        return True
    if nums.replace("1", "") == "" and len(nums) >= 2:
        return True
    if nums in {"111", "117", "711", "171", "11"}:
        return True
    if letters in {"DD", "HID", "ID", "II", "HH", "DI", "HD", "LL", "PEF", "FEE", "EEE"}:
        return True
    if letters.count("P") + letters.count("F") >= 2:
        return True
    return False


def _looks_truncated(plate: str) -> bool:
    """2 harf + 3 hane sıkça 4. hanesi kesilmiş okumadır (34 FV 675 / 6754).
    3 harf + 2 hane (16 CAL 75) geçerli kalıptır, eksik sayılmaz."""
    _c, letters, nums = plate.split()
    if len(letters) == 2 and len(nums) <= 3:
        return True
    if len(letters) == 1 and len(nums) < 4:
        return True
    return False


def _in_headlight_zone(x: int, y: int, cw: int, ch: int, w: int, h: int) -> bool:
    """Yakın tamponda sol/sağ far — plaka alt-orta."""
    if w <= 0 or h <= 0:
        return True
    cx = (x + cw / 2.0) / w
    cy = (y + ch / 2.0) / h
    if cw > w * 0.38 or ch > h * 0.20:
        return True
    if cy < 0.64 and (cx < 0.32 or cx > 0.66):
        return True
    return False


def parse_city(tok: str) -> str | None:
    t = clean_token(tok)
    if len(t) >= 2 and t[-2:].isdigit() and t[-2:] in VALID_CITY:
        return t[-2:]
    if t.isdigit() and len(t) == 1:
        cand = f"0{t}"
        if cand in VALID_CITY:
            return cand
    return None


def parse_letters(tok: str) -> str | None:
    t = clean_token(tok)
    if 1 <= len(t) <= 3 and t.isalpha():
        return t
    return None


def parse_numbers(tok: str) -> str | None:
    t = clean_token(tok)
    if t.isdigit() and 2 <= len(t) <= 4:
        return t
    return None


def assemble_plates_from_tokens(results: list) -> list[tuple[str, float, str]]:
    items = []
    for bbox, text, conf in results:
        if conf < 0.12:
            continue
        if is_junk_text(text) or text.strip().upper() in {"IPC", "TR"}:
            continue
        ct = clean_token(text)
        if len(ct) >= 5 and ct.isalpha():
            continue
        try:
            xs = [p[0] for p in bbox]
            ys = [p[1] for p in bbox]
            cx, cy = sum(xs) / 4.0, sum(ys) / 4.0
        except Exception:
            cx, cy = 0.0, 0.0
        items.append((cx, cy, text, float(conf)))

    items.sort(key=lambda x: (round(x[1] / 40), x[0]))
    found: list[tuple[str, float, str]] = []
    n = len(items)

    for i in range(n):
        for j in range(i + 1, min(i + 5, n)):
            for k in range(j + 1, min(j + 5, n)):
                if abs(items[i][1] - items[j][1]) > 90:
                    continue
                if abs(items[j][1] - items[k][1]) > 90:
                    continue
                toks = [items[i][2], items[j][2], items[k][2]]
                confs = [items[i][3], items[j][3], items[k][3]]
                city = letters = nums = None
                used: set[int] = set()
                for ti, tok in enumerate(toks):
                    c = parse_city(tok)
                    if c and city is None:
                        city = c
                        used.add(ti)
                for ti, tok in enumerate(toks):
                    if ti in used:
                        continue
                    L = parse_letters(tok)
                    if L and letters is None:
                        letters = L
                        used.add(ti)
                for ti, tok in enumerate(toks):
                    if ti in used:
                        continue
                    nn = parse_numbers(tok)
                    if nn and nums is None:
                        nums = nn
                        used.add(ti)
                if city and letters and nums and city in VALID_CITY:
                    found.append(
                        (f"{city} {letters} {nums}", sum(confs) / 3.0, " ".join(toks))
                    )

    for i in range(n):
        for j in range(i + 1, min(i + 4, n)):
            if abs(items[i][1] - items[j][1]) > 90:
                continue
            a, b = clean_token(items[i][2]), clean_token(items[j][2])
            for joined in (a + b, b + a):
                plate = normalize_plate(joined)
                if plate:
                    found.append(
                        (
                            plate,
                            (items[i][3] + items[j][3]) / 2.0,
                            f"{items[i][2]}|{items[j][2]}",
                        )
                    )
    return found


def _quiet_stderr():
    """FFmpeg PPS uyarilarini gizle (akisa zarar vermez)."""

    class _Ctx:
        def __enter__(self):
            self._devnull = os.open(os.devnull, os.O_WRONLY)
            self._old = os.dup(2)
            os.dup2(self._devnull, 2)
            return self

        def __exit__(self, *args):
            os.dup2(self._old, 2)
            os.close(self._old)
            os.close(self._devnull)
            return False

    return _Ctx()


def open_cam(
    url: str,
    low_latency: bool = True,
    open_ms: int = 8000,
    read_ms: int = 8000,
) -> cv2.VideoCapture:
    os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = (
        "rtsp_transport;tcp|allowed_media_types;video|fflags;discardcorrupt|"
        "stimeout;5000000|rw_timeout;5000000"
    )
    with _quiet_stderr():
        cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
    try:
        if low_latency:
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        cap.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, open_ms)
        cap.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC, read_ms)
    except Exception:
        pass
    return cap


def grab(cap: cv2.VideoCapture, tries: int = 8, need: int = 1):
    """Ilk H264 kareleri PPS'siz olabilir; gecerli kare gelene kadar atla."""
    frame = None
    got = 0
    with _quiet_stderr():
        for _ in range(tries):
            ok, f = cap.read()
            if ok and f is not None and f.size > 0:
                frame = f
                got += 1
                if got >= need:
                    break
    return frame


def grab_brief(url: str, tries: int = 8, timeout_ms: int = 2000):
    """Ana yayini kisa ac, anahtar kare bekle, hemen birak (Neutron tek oturum)."""
    cap = open_cam(url, low_latency=False, open_ms=timeout_ms, read_ms=timeout_ms)
    frame = grab(cap, tries=tries, need=2) if cap.isOpened() else None
    try:
        cap.release()
    except Exception:
        pass
    del cap
    return frame


class NeutronSnap:
    """165 RTSP ana döngüyü kilitlemesin — 153 okuması bekler."""

    def __init__(self, url: str):
        self.url = url
        self._lock = threading.Lock()
        self._frame: np.ndarray | None = None
        self._ts = 0.0
        self._busy = False
        self._stop = threading.Event()
        self._kick = threading.Event()
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="neutron-165"
        )
        self._thread.start()

    def set_busy(self, busy: bool) -> None:
        was = self._busy
        self._busy = bool(busy)
        if busy and not was:
            self._kick.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            t0 = time.time()
            frame = grab_brief(self.url)
            if frame is not None and frame_usable(frame):
                frame = shrink_ocr_frame(frame)
                with self._lock:
                    self._frame = frame
                    self._ts = time.time()
            gap = 0.45 if self._busy else 3.5
            self._kick.wait(max(0.05, gap - (time.time() - t0)))
            self._kick.clear()

    def latest(self) -> np.ndarray | None:
        with self._lock:
            if self._frame is None:
                return None
            return self._frame.copy()

    def is_fresh(self, max_age: float = 4.0) -> bool:
        with self._lock:
            return self._frame is not None and (time.time() - self._ts) <= max_age

    def stop(self) -> None:
        self._stop.set()
        self._kick.set()


class HoldStream:
    """Dahua alt yayinini surekli oku — OCR beklerken tampon dolup kesilmesin."""

    def __init__(self, url: str):
        self.url = url
        self._lock = threading.Lock()
        self._frame: np.ndarray | None = None
        self._ok = False
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="rtsp-hold"
        )
        self._thread.start()

    def _loop(self) -> None:
        cap = None
        miss = 0
        while not self._stop.is_set():
            if cap is None:
                cap = open_cam(self.url)
                if not cap.isOpened():
                    try:
                        cap.release()
                    except Exception:
                        pass
                    cap = None
                    self._ok = False
                    self._stop.wait(2.0)
                    continue
                self._ok = True
            with _quiet_stderr():
                ok, f = cap.read()
            if ok and f is not None and f.size > 0:
                miss = 0
                with self._lock:
                    self._frame = f
            else:
                miss += 1
                if miss >= 40:
                    try:
                        cap.release()
                    except Exception:
                        pass
                    cap = None
                    self._ok = False
                    miss = 0

        if cap is not None:
            try:
                cap.release()
            except Exception:
                pass

    def latest(self) -> np.ndarray | None:
        with self._lock:
            if self._frame is None:
                return None
            return self._frame.copy()

    def is_open(self) -> bool:
        return self._ok and self._frame is not None

    def stop(self) -> None:
        self._stop.set()


def gray_roi(frame: np.ndarray) -> np.ndarray:
    h, w = frame.shape[:2]
    roi = frame[int(h * 0.35) : int(h * 0.95), int(w * 0.1) : int(w * 0.9)]
    g = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    return cv2.GaussianBlur(g, (5, 5), 0)


def scene_busy(frame: np.ndarray) -> float:
    """Doluluk: bos beton dusuk, kamyon ızgarası yuksek."""
    return float(gray_roi(frame).std())


BUSY_STD = 14.0
EMPTY_BUSY = 36.0
VEHICLE_DIFF_OCR = 16.0
LENS_SHARP_MIN = 14.0
BG_WARMUP = 6
VEHICLE_COVER = 16.0
BG_ADAPT = 6.0
# Giriş 153 ve çıkış 165 aynı kamera eşiği — kantardan bağımsız.
COVER_MIN = {
    "153": 16.0,
    "165": 22.0,
}
PRESENCE_ON = 1
PRESENCE_OFF = 3
TEXTURE_MIN = {
    "153": 3.5,
    "165": 6.0,
}


def lens_health(frame: np.ndarray) -> tuple[float, str]:
    """Camur/sis: netlik dusukse lens veya hava kirli."""
    g = gray_roi(frame)
    sharp = float(cv2.Laplacian(g, cv2.CV_64F).var())
    mean = float(g.mean())
    if sharp < LENS_SHARP_MIN:
        return sharp, "bulanik"
    if mean < 32:
        return sharp, "karanlik"
    if mean > 215:
        return sharp, "parlama"
    return sharp, "ok"


def mask_overlays(frame: np.ndarray) -> np.ndarray:
    """Sag ust tarih/saat, sol ust onceki plaka yazisi, sol alt IPC."""
    img = frame.copy()
    h, w = img.shape[:2]
    img[: int(h * 0.18), int(w * 0.48) :] = 0
    img[: int(h * 0.18), : int(w * 0.48)] = 0
    img[int(h * 0.90) :, : int(w * 0.18)] = 0
    return img


def has_tr_band(frame: np.ndarray) -> bool:
    """Bos zeminde yok; plakadaki mavi TR seridi veya harf cubugu = arac var."""
    if find_plate_boxes(frame):
        return True
    h, w = frame.shape[:2]
    roi = frame[int(h * 0.38) :, :]
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    blue = cv2.inRange(hsv, (90, 50, 40), (140, 255, 255))
    blue = cv2.morphologyEx(blue, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
    cnts, _ = cv2.findContours(blue, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    rh = roi.shape[0]
    for c in cnts:
        x, y, cw, ch = cv2.boundingRect(c)
        if 16 <= ch <= rh * 0.40 and 6 <= cw <= ch * 1.8:
            return True
    return False


def _letter_like_count(gray: np.ndarray) -> int:
    """Plaka harfi boyutunda koyu lekeleri say — CLAHE+adaptive (tozlu plaka)."""
    if gray.size < 80:
        return 0
    if gray.ndim == 3:
        gray = cv2.cvtColor(gray, cv2.COLOR_BGR2GRAY)
    clahe = cv2.createCLAHE(clipLimit=2.8, tileGridSize=(8, 8)).apply(gray)
    work = clahe if float(clahe.mean()) >= 105 else cv2.bitwise_not(clahe)
    th = cv2.adaptiveThreshold(
        work, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 21, 8
    )
    n, _, stats, _ = cv2.connectedComponentsWithStats(th)
    h, _w = gray.shape[:2]
    cnt = 0
    for i in range(1, n):
        ww = int(stats[i, cv2.CC_STAT_WIDTH])
        hh = int(stats[i, cv2.CC_STAT_HEIGHT])
        area = int(stats[i, cv2.CC_STAT_AREA])
        if hh >= h * 0.22 and hh <= h * 0.98 and 2 <= ww <= h * 1.4 and area >= 8:
            cnt += 1
    return cnt


def _tr_band_plate_boxes(frame: np.ndarray) -> list[tuple[float, int, int, int, int]]:
    """İnce mavi TR şeridi; far halkasını dev dikdörtgene şişirme."""
    h, w = frame.shape[:2]
    yoff = int(h * 0.32)
    roi = frame[yoff : int(h * 0.95)]
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    blue = cv2.inRange(hsv, (90, 40, 40), (140, 255, 255))
    blue = cv2.morphologyEx(blue, cv2.MORPH_CLOSE, np.ones((9, 5), np.uint8))
    cnts, _ = cv2.findContours(blue, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    out = []
    for c in cnts:
        x, y, cw, ch = cv2.boundingRect(c)
        if not (18 <= ch <= min(90, int(h * 0.13))):
            continue
        if not (5 <= cw <= min(24, int(ch * 0.55))):
            continue
        if ch < cw * 1.7:
            continue
        px = max(0, x - 2)
        py = max(0, yoff + y - 6)
        pw = min(w - px, int(ch * 7.2) + 28)
        ph = min(h - py, int(ch * 1.3) + 10)
        if pw < 90 or ph < 24 or pw > int(w * 0.34) or ph > int(h * 0.16):
            continue
        if _in_headlight_zone(px, py, pw, ph, w, h):
            continue
        g = cv2.cvtColor(frame[py : py + ph, px : px + pw], cv2.COLOR_BGR2GRAY)
        lets = _letter_like_count(g)
        if lets < 4:
            continue
        out.append((80.0 + lets * 10.0 + float(ch), px, py, pw, ph))
    return out


def _bumper_plate_boxes(
    frame: np.ndarray, rear: bool = False
) -> list[tuple[float, int, int, int, int]]:
    """Ön tampon alt-orta; arka kamerada kasa / sağ kenar da taransın."""
    h, w = frame.shape[:2]
    if rear:
        y0, y1 = int(h * 0.28), min(h, int(h * 0.90))
        x0, x1 = int(w * 0.04), int(w * 0.97)
    else:
        y0, y1 = int(h * 0.55), min(h, int(h * 0.92))
        x0, x1 = int(w * 0.22), int(w * 0.78)
    roi = frame[y0:y1, x0:x1]
    if roi.size < 400:
        return []
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    gx = cv2.convertScaleAbs(cv2.Sobel(gray, cv2.CV_16S, 1, 0, ksize=3))
    rh, rw = gray.shape[:2]
    scored: list[tuple[float, int, int, int, int]] = []
    for win_h in (28, 36, 44, 56, 70):
        win_w = int(win_h * 6.4)
        if rh < win_h or rw < win_w:
            continue
        step_y = max(6, win_h // 4)
        step_x = max(10, win_w // 5)
        for y in range(0, rh - win_h + 1, step_y):
            for x in range(0, rw - win_w + 1, step_x):
                edge = float(gx[y : y + win_h, x : x + win_w].mean())
                if edge < 12:
                    continue
                patch = gray[y : y + win_h, x : x + win_w]
                st = float(patch.std())
                if st < 26:
                    continue
                lets = _letter_like_count(patch)
                if lets < 5:
                    continue
                scored.append(
                    (
                        lets * 8.0 + edge * 0.4 + st * 0.3,
                        x0 + x,
                        y0 + y,
                        win_w,
                        win_h,
                    )
                )
    scored.sort(reverse=True)
    return scored[:8]


def _light_rect_boxes(
    frame: np.ndarray, rear: bool = False
) -> list[tuple[float, int, int, int, int]]:
    """Açık plaka dikdörtgeni."""
    h, w = frame.shape[:2]
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, (0, 0, 120), (180, 90, 255))
    if rear:
        mask[: int(h * 0.24), :] = 0
        mask[int(h * 0.92) :, :] = 0
    else:
        mask[: int(h * 0.55), :] = 0
        mask[int(h * 0.92) :, :] = 0
        mask[:, : int(w * 0.22)] = 0
        mask[:, int(w * 0.78) :] = 0
    mask = cv2.morphologyEx(
        mask, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (9, 5))
    )
    cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    max_h = max(90, int(h * 0.16))
    max_w = min(480, int(w * 0.42))
    out = []
    for c in cnts:
        x, y, cw, ch = cv2.boundingRect(c)
        ar = cw / max(ch, 1)
        if ch < 16 or ch > max_h or cw < 80 or cw > max_w:
            continue
        if not (2.3 <= ar <= 7.8):
            continue
        g = cv2.cvtColor(frame[y : y + ch, x : x + cw], cv2.COLOR_BGR2GRAY)
        lets = _letter_like_count(g)
        if lets < 4:
            continue
        out.append((40.0 + lets * 8.0 + float(g.std()) * 0.2, x, y, cw, ch))
    return out


def find_plate_boxes(
    frame: np.ndarray, rear: bool = False
) -> list[tuple[int, int, int, int]]:
    """Ön: tampon alt-orta. Arka: kasa ve kenar plaka."""
    h, w = frame.shape[:2]
    scored = (
        _bumper_plate_boxes(frame, rear=rear)
        + _tr_band_plate_boxes(frame)
        + _light_rect_boxes(frame, rear=rear)
    )
    if not rear:
        scored = [
            t
            for t in scored
            if not _in_headlight_zone(t[1], t[2], t[3], t[4], w, h)
        ]
    scored.sort(reverse=True)
    picked: list[tuple[int, int, int, int]] = []
    for _s, x, y, ww, hh in scored:
        if any(
            abs(x - px) < max(ww, pw) * 0.45 and abs(y - py) < max(hh, ph) * 0.55
            for px, py, pw, ph in picked
        ):
            continue
        picked.append((x, y, ww, hh))
        if len(picked) >= 3:
            break
    picked.sort(key=lambda b: -b[1])
    if picked:
        return picked
    y0, y1 = (int(h * 0.28), min(h, int(h * 0.90))) if rear else (int(h * 0.55), min(h, int(h * 0.92)))
    gray = cv2.cvtColor(frame[y0:y1, :], cv2.COLOR_BGR2GRAY)
    gx = cv2.convertScaleAbs(cv2.Sobel(gray, cv2.CV_16S, 1, 0, ksize=3))
    rh, rw = gray.shape[:2]
    extra: list[tuple[float, int, int, int, int]] = []
    for win_h in (24, 32, 40, 50, 64):
        win_w = int(win_h * 5.3)
        if rh < win_h or rw < win_w:
            continue
        step_y = max(8, win_h // 3)
        step_x = max(16, win_w // 4)
        for y in range(0, rh - win_h + 1, step_y):
            for x in range(0, rw - win_w + 1, step_x):
                if (not rear) and _in_headlight_zone(x, y + y0, win_w, win_h, w, h):
                    continue
                edge = float(gx[y : y + win_h, x : x + win_w].mean())
                if edge < 14:
                    continue
                patch = gray[y : y + win_h, x : x + win_w]
                if float(patch.std()) < 24:
                    continue
                lets = _letter_like_count(patch)
                if lets < 5:
                    continue
                extra.append((lets * 8.0 + edge * 0.35, x, y + y0, win_w, win_h))
    extra.sort(reverse=True)
    for _s, x, y, ww, hh in extra:
        if any(
            abs(x - px) < max(ww, pw) * 0.45 and abs(y - py) < max(hh, ph) * 0.55
            for px, py, pw, ph in picked
        ):
            continue
        picked.append((x, y, ww, hh))
        if len(picked) >= 3:
            break
    return picked


def _merge_box_clusters(
    boxes: list[tuple[int, int, int, int]]
) -> list[tuple[int, int, int, int]]:
    """Aynı plakadaki parçalı kutuları birleştir (34… ve …953)."""
    if not boxes:
        return []
    used = [False] * len(boxes)
    merged: list[tuple[int, int, int, int]] = []
    for i, (x, y, w, h) in enumerate(boxes):
        if used[i]:
            continue
        xa, ya, xb, yb = x, y, x + w, y + h
        used[i] = True
        changed = True
        while changed:
            changed = False
            for j, (x2, y2, w2, h2) in enumerate(boxes):
                if used[j]:
                    continue
                cy = (ya + yb) / 2
                cy2 = y2 + h2 / 2
                if abs(cy - cy2) > max(h, h2) * 0.9:
                    continue
                if x2 > xb + w * 0.25 or xa > x2 + w2 + w * 0.25:
                    continue
                nxa, nya = min(xa, x2), min(ya, y2)
                nxb, nyb = max(xb, x2 + w2), max(yb, y2 + h2)
                if nxb - nxa > 300:
                    continue
                xa, ya, xb, yb = nxa, nya, nxb, nyb
                used[j] = True
                changed = True
        merged.append((xa, ya, xb - xa, yb - ya))
    return merged[:6]


def _letter_row(crop: np.ndarray) -> np.ndarray:
    """Alt etiket / vida satırını bırak, yalnız karakter şeridini al."""
    h, w = crop.shape[:2]
    if h < 28:
        return crop
    g = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.ndim == 3 else crop
    gx = cv2.convertScaleAbs(cv2.Sobel(g, cv2.CV_16S, 1, 0, ksize=3))
    proj = gx.mean(axis=1)
    band = max(22, min(40, int(w / 5.4)))
    best_y, best = 0, -1.0
    for y in range(0, h - band + 1):
        s = float(proj[y : y + band].mean())
        if s > best:
            best, best_y = s, y
    return crop[best_y : best_y + band, :]


def _plate_crop(frame: np.ndarray, x: int, y: int, cw: int, ch: int) -> np.ndarray:
    """Sadece plaka yazısı: etiket ve stop lambası kadraja girmez."""
    h, w = frame.shape[:2]
    letter_h = int(max(26, min(72, cw / 4.5)))
    if ch > letter_h * 1.25:
        ch = letter_h
    ya = max(0, y - 3)
    yb = min(h, ya + ch + 6)
    xa = max(0, x - 18)
    xb = min(w, x + cw + 28)
    if (x + cw) >= int(w * 0.78):
        xb = w
        xa = max(0, x - 4)
    return frame[ya:yb, xa:xb]


def _tighten_crop(crop: np.ndarray) -> list[np.ndarray]:
    h, w = crop.shape[:2]
    if h <= 56 and w <= 320:
        return [crop]
    inner = find_plate_boxes(crop)
    if inner:
        return [_plate_crop(crop, x, y, ww, hh) for x, y, ww, hh in inner[:2]]
    return [_plate_crop(crop, 0, 0, w, h)]


def find_plate_crops(frame: np.ndarray, rear: bool = False) -> list[np.ndarray]:
    """Sıkı plaka kutuları. Boş zeminde yedek kırpma yok — OCR uydurmasın."""
    h, w = frame.shape[:2]
    out: list[np.ndarray] = []
    for x, y, ww, hh in find_plate_boxes(frame, rear=rear):
        crop = frame[y : y + hh, x : x + ww]
        if crop.size < 800:
            continue
        if crop.shape[0] > 70:
            crop = _letter_row(_plate_crop(frame, x, y, ww, hh))
        if crop.size >= 800:
            out.append(crop)
        if len(out) >= 5:
            return out
    if out:
        return out
    if scene_busy(frame) < 32.0:
        return out
    if rear:
        fallbacks = [
            (int(w * 0.08), int(h * 0.32), int(w * 0.84), int(h * 0.50)),
        ]
    else:
        fallbacks = [
            (int(w * 0.28), int(h * 0.62), int(w * 0.44), int(h * 0.18)),
        ]
    for x, y, ww, hh in fallbacks:
        for crop in _tighten_crop(frame[y : y + hh, x : x + ww]):
            if crop is None or crop.size < 800:
                continue
            if crop.shape[0] > 56:
                crop = _plate_crop(crop, 0, 0, crop.shape[1], crop.shape[0])
            out.append(crop)
            if len(out) >= 5:
                return out
    return out


def enhance_plate(crop: np.ndarray) -> list[np.ndarray]:
    """Boya/kontrast + kabartı (çamurda harf yüksekliği)."""
    up = cv2.resize(crop, None, fx=3.0, fy=3.0, interpolation=cv2.INTER_CUBIC)
    g = cv2.cvtColor(up, cv2.COLOR_BGR2GRAY)
    g = cv2.bilateralFilter(g, 5, 30, 30)
    clahe = cv2.createCLAHE(clipLimit=3.5, tileGridSize=(8, 8))
    en = clahe.apply(g)
    gamma = np.uint8(np.clip((en / 255.0) ** 0.55 * 255.0, 0, 255))
    blur = cv2.GaussianBlur(gamma, (0, 0), 1.0)
    sharp = cv2.addWeighted(gamma, 1.6, blur, -0.6, 0)
    return [
        up,
        cv2.cvtColor(sharp, cv2.COLOR_GRAY2BGR),
        cv2.cvtColor(relief_letters(g), cv2.COLOR_GRAY2BGR),
    ]


def relief_letters(gray: np.ndarray) -> np.ndarray:
    """Kabartılı karakter: morfolojik gradyan + black-hat (3D sırt)."""
    ridge = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    char = cv2.getStructuringElement(cv2.MORPH_RECT, (19, 5))
    grad = cv2.morphologyEx(gray, cv2.MORPH_GRADIENT, ridge)
    hole = cv2.morphologyEx(gray, cv2.MORPH_BLACKHAT, char)
    bump = cv2.addWeighted(grad, 0.65, hole, 1.0, 0)
    bump = cv2.normalize(bump, None, 0, 255, cv2.NORM_MINMAX)
    inv = cv2.bitwise_not(bump)
    inv = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(inv)
    _, th = cv2.threshold(inv, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return th


def tune_camera_image(ip: str) -> str:
    """Dahua CGI: kontrast — kabartı gölgesi kamerada belirginleşsin."""
    import base64

    auth = base64.b64encode(f"{USER}:{PASS}".encode()).decode()
    q = (
        "VideoColor[0][0].Contrast=62&VideoColor[0][0].Brightness=48"
        "&VideoColor[0][1].Contrast=62&VideoColor[0][1].Brightness=48"
    )
    url = f"http://{ip}/cgi-bin/configManager.cgi?action=setConfig&{q}"
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Basic {auth}")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            body = resp.read().decode("utf-8", errors="replace").strip()
            return body or f"HTTP {resp.status}"
    except Exception as e:
        return type(e).__name__


def _read_jpeg_bytes(resp, limit: int = 2_500_000) -> bytes:
    """MJPEG/sürekli akışta ilk JPEG ile dur; resp.read() asılı kalmasın."""
    buf = bytearray()
    while len(buf) < limit:
        chunk = resp.read(65536)
        if not chunk:
            break
        buf.extend(chunk)
        if buf[:2] == b"\xff\xd8":
            end = buf.find(b"\xff\xd9")
            if end >= 0:
                return bytes(buf[: end + 2])
        elif len(buf) > 32:
            break
    return bytes(buf)


def _dahua_jpeg(ip: str, timeout: float = 1.2):
    """Kameranın kendi HTTP karesi — RTSP yok."""
    import base64

    try:
        auth = base64.b64encode(f"{USER}:{PASS}".encode()).decode()
        req = urllib.request.Request(f"http://{ip}/cgi-bin/snapshot.cgi")
        req.add_header("Authorization", f"Basic {auth}")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = _read_jpeg_bytes(resp)
        if data[:2] != b"\xff\xd8":
            return None
        arr = np.frombuffer(data, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if img is not None and getattr(img, "size", 0) > 0:
            return img
    except Exception:
        return None
    return None


def dahua_snapshot(ip: str, wait: float = 0.8):
    """Tek seferlik HTTP kare — kısa bekler."""
    out: list = [None]

    def _pull() -> None:
        out[0] = _dahua_jpeg(ip, timeout=min(2.0, max(0.4, wait)))

    t = threading.Thread(target=_pull, daemon=True)
    t.start()
    t.join(max(0.15, wait))
    return out[0]


class DeviceSnap:
    """Dahua cihazından sürekli JPEG — yayın tamponu / GOP gecikmesi yok."""

    def __init__(self, ip: str, interval: float = 0.06):
        self.ip = ip
        self.interval = interval
        self._lock = threading.Lock()
        self._frame: np.ndarray | None = None
        self._ts = 0.0
        self._ok = False
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name=f"dev-{ip.split('.')[-1]}"
        )
        self._thread.start()

    def _loop(self) -> None:
        while not self._stop.is_set():
            img = _dahua_jpeg(self.ip, timeout=0.7)
            if img is not None:
                img = shrink_ocr_frame(img)
                with self._lock:
                    self._frame = img
                    self._ts = time.time()
                self._ok = True
                self._stop.wait(self.interval)
            else:
                self._ok = False
                self._stop.wait(0.20)

    def latest(self) -> np.ndarray | None:
        with self._lock:
            if self._frame is None:
                return None
            return self._frame.copy()

    def age(self) -> float:
        with self._lock:
            if self._ts <= 0:
                return 1e9
            return time.time() - self._ts

    def is_open(self) -> bool:
        return self._ok and self._frame is not None

    def stop(self) -> None:
        self._stop.set()


def shrink_ocr_frame(frame: np.ndarray | None, max_w: int = 960):
    """İlk okumayı hızlandır — 4K/1280 YOLO'yu yavaşlatır."""
    if frame is None or getattr(frame, "size", 0) == 0:
        return frame
    h, w = frame.shape[:2]
    if w <= max_w:
        return frame
    return cv2.resize(frame, (max_w, max(1, int(h * max_w / w))), interpolation=cv2.INTER_AREA)


ALLOW_ALL = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
ALLOW_D = "0123456789"
ALLOW_L = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"

_RAPID = None
_ALPR = None


class _YoloPlateDet:
    """YOLOv9 plaka kutusu — çit/boru/far değil, plaka."""

    def __init__(self, path: Path, conf: float) -> None:
        from open_image_models.detection.factory import create_detector

        self._d = create_detector(
            str(path),
            backend="yolo_v9",
            class_labels=("License Plate",),
            conf_thresh=conf,
        )

    def predict(self, frame: np.ndarray):
        return self._d.predict(frame)


def _init_alpr() -> str:
    """2026 FastALPR: YOLO plaka tespiti + CCT plaka OCR."""
    global _ALPR
    try:
        from fast_alpr import ALPR

        det = MODELS_DIR / "yolo-plate" / "yolo-v9-t-512-license-plates-end2end.onnx"
        ocr = MODELS_DIR / "plate-ocr" / "cct_s_v2_global.onnx"
        cfg = MODELS_DIR / "plate-ocr" / "cct_s_v2_global_plate_config.yaml"
        if not det.is_file() or not ocr.is_file() or not cfg.is_file():
            _ALPR = None
            return "ALPR model dosyasi yok"
        _ALPR = ALPR(
            detector=_YoloPlateDet(det, 0.28),
            ocr_model=None,
            ocr_model_path=str(ocr),
            ocr_config_path=str(cfg),
            ocr_device="cpu",
        )
        return "YOLO-v9 plaka + CCT-S OCR"
    except Exception as exc:
        _ALPR = None
        return f"ALPR yok ({type(exc).__name__})"


def _ocr_mean_conf(conf) -> float:
    if conf is None:
        return 0.0
    if isinstance(conf, (list, tuple)) and conf:
        return float(sum(float(x) for x in conf) / len(conf))
    try:
        return float(conf)
    except (TypeError, ValueError):
        return 0.0


def _init_rapid() -> str:
    """Paddle/ONNX satır OCR — EasyOCR'den net plaka için."""
    global _RAPID
    try:
        from rapidocr_onnxruntime import RapidOCR

        _RAPID = RapidOCR()
        return "RapidOCR"
    except Exception as exc:
        _RAPID = None
        return f"RapidOCR yok ({type(exc).__name__})"


def _rapid_read(img: np.ndarray) -> list:
    if _RAPID is None or img is None or img.size < 80:
        return []
    bgr = img if img.ndim == 3 else cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    try:
        packed = _RAPID(bgr)
    except Exception:
        return []
    result = packed[0] if isinstance(packed, tuple) else packed
    if not result:
        return []
    hh, ww = bgr.shape[:2]
    box = [[0, 0], [ww, 0], [ww, hh], [0, hh]]
    found = []
    for item in result:
        if not item or len(item) < 2:
            continue
        text = str(item[1])
        conf = float(item[2]) if len(item) > 2 else 0.5
        found.append((box, text, conf))
    return found


def _recognize_line(reader, gray: np.ndarray, allow: str) -> list:
    """CRAFT atlanır — kırpılmış satırı tek satır olarak oku."""
    if reader is None or gray is None or gray.size < 80:
        return []
    if gray.ndim == 3:
        gray = cv2.cvtColor(gray, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape[:2]
    scale = max(2.4, 72.0 / max(h, 1))
    up = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    hh, ww = up.shape[:2]
    try:
        out = reader.recognize(
            up,
            horizontal_list=[[0, ww, 0, hh]],
            free_list=[],
            allowlist=allow,
            detail=1,
        )
    except Exception:
        return []
    box = [[0, 0], [ww, 0], [ww, hh], [0, hh]]
    found = []
    for item in out:
        if len(item) < 2:
            continue
        text = str(item[1])
        conf = float(item[2]) if len(item) > 2 else 0.0
        found.append((box, text, conf))
    return found


def _ocr_line_variants(reader, crop: np.ndarray) -> list:
    results = []
    results.extend(_rapid_read(crop))
    if crop.shape[0] > 52:
        crop = _letter_row(crop)
    results.extend(_rapid_read(crop))
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    clahe = cv2.createCLAHE(clipLimit=2.8, tileGridSize=(8, 8)).apply(gray)
    variants = [gray, clahe]
    if float(gray.mean()) < 115:
        variants.append(cv2.bitwise_not(clahe))
    for im in variants:
        results.extend(_recognize_line(reader, im, ALLOW_ALL))
    return results


def _trim_letters(s: str) -> str:
    if len(s) <= 3:
        return s
    for ch in ("I", "1"):
        if ch in s:
            t = s.replace(ch, "", 1)
            if 1 <= len(t) <= 3:
                return t
    return s[:3]


def _ocr_char(reader, chip: np.ndarray, allow: str) -> tuple[str, float]:
    if chip.size < 20:
        return "", 0.0
    if chip.ndim == 3:
        chip = cv2.cvtColor(chip, cv2.COLOR_BGR2GRAY)
    pad = max(4, chip.shape[0] // 6)
    chip = cv2.copyMakeBorder(chip, pad, pad, pad, pad, cv2.BORDER_CONSTANT, value=255)
    got = _recognize_line(reader, chip, allow)
    if not got:
        return "", 0.0
    return got[0][1], float(got[0][2])


def _ocr_segments(reader, crop: np.ndarray) -> list:
    """Karakter lekelerini D/L diye ayır, TR plaka kalıbına diz."""
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    gray = cv2.createCLAHE(clipLimit=2.8, tileGridSize=(8, 8)).apply(gray)
    work = gray if float(gray.mean()) >= 105 else cv2.bitwise_not(gray)
    adaptive = cv2.adaptiveThreshold(
        work, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 21, 8
    )
    _, otsu = cv2.threshold(work, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    results = []
    for th in (adaptive, otsu):
        th = cv2.morphologyEx(th, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
        got = _ocr_segments_from_th(reader, work, th)
        results.extend(got)
        if any(
            _plate_from_token(clean_token(t)) or normalize_plate(t) for _b, t, _c in got
        ):
            break
    return results


def _ocr_segments_from_th(reader, work: np.ndarray, th: np.ndarray) -> list:
    n, _lab, stats, _ = cv2.connectedComponentsWithStats(th)
    h, w = th.shape[:2]
    boxes: list[tuple[int, int, int, int]] = []
    for i in range(1, n):
        x = int(stats[i, cv2.CC_STAT_LEFT])
        y = int(stats[i, cv2.CC_STAT_TOP])
        cw = int(stats[i, cv2.CC_STAT_WIDTH])
        ch = int(stats[i, cv2.CC_STAT_HEIGHT])
        area = int(stats[i, cv2.CC_STAT_AREA])
        if ch < h * 0.22 or ch > h * 0.98 or cw < 2 or cw > h * 1.4 or area < 8:
            continue
        boxes.append((x, y, cw, ch))
    boxes.sort(key=lambda b: b[0])
    merged: list[tuple[int, int, int, int]] = []
    for b in boxes:
        if merged and b[0] < merged[-1][0] + merged[-1][2] * 0.45:
            x0 = min(merged[-1][0], b[0])
            y0 = min(merged[-1][1], b[1])
            x1 = max(merged[-1][0] + merged[-1][2], b[0] + b[2])
            y1 = max(merged[-1][1] + merged[-1][3], b[1] + b[3])
            merged[-1] = (x0, y0, x1 - x0, y1 - y0)
        else:
            merged.append(b)
    if not (5 <= len(merged) <= 12):
        return []

    parts: list[tuple[str, str, float]] = []
    for x, y, cw, ch in merged[:10]:
        chip = work[
            max(0, y - 2) : min(h, y + ch + 2),
            max(0, x - 2) : min(w, x + cw + 2),
        ]
        d, dc = _ocr_char(reader, chip, ALLOW_D)
        L, Lc = _ocr_char(reader, chip, ALLOW_L)
        if max(dc, Lc) < 0.22:
            continue
        if dc >= Lc and dc >= 0.35:
            parts.append(("D", clean_token(d), dc))
        else:
            parts.append(("L", clean_token(L), Lc))

    groups: list[tuple[str, str, float]] = []
    for typ, text, conf in parts:
        if not text:
            continue
        if groups and groups[-1][0] == typ:
            groups[-1] = (typ, groups[-1][1] + text, min(groups[-1][2], conf))
        else:
            groups.append((typ, text, conf))

    results = []
    box = [[0, 0], [w, 0], [w, h], [0, h]]
    for i in range(len(groups) - 2):
        if groups[i][0] != "D" or groups[i + 1][0] != "L" or groups[i + 2][0] != "D":
            continue
        city, letters, nums = groups[i][1], _trim_letters(groups[i + 1][1]), groups[i + 2][1][:4]
        if len(city) >= 2:
            city = city[:2] if city[:2] in VALID_CITY else city[-2:]
        raw = city + letters + nums
        conf = (groups[i][2] + groups[i + 1][2] + groups[i + 2][2]) / 3.0
        results.append((box, raw, conf))
    return results


def read_plates(
    reader,
    frame: np.ndarray,
    rear: bool = True,
    open_keys: set[str] | None = None,
    seated: bool = False,
    deadline: float | None = None,
) -> list[tuple[str, float, str]]:
    """YOLO plaka kutusu + CCT OCR; yoksa RapidOCR. Saat/çit okunmaz."""
    t0 = time.time()

    def _ok() -> bool:
        return deadline is None or (time.time() - t0) < deadline

    work = mask_overlays(frame)
    best: dict[str, tuple[float, str]] = {}

    def add(plate: str, conf: float, raw: str, bonus: float = 0.0) -> None:
        if is_clock_plate(plate):
            return
        q = plate_quality(plate, conf) + bonus
        if clean_token(raw) == plate.replace(" ", ""):
            q += 0.20
        if is_weak_plate(plate, q):
            return
        prev = best.get(plate)
        if not prev or q > prev[0]:
            best[plate] = (q, raw)

    def ingest(results: list, min_conf: float, bonus: float = 0.0) -> None:
        for _b, text, conf in results:
            if conf < min_conf or is_junk_text(text):
                continue
            plate = normalize_plate(text) or normalize_plate(clean_token(text))
            if not plate and open_keys:
                plate = complete_open_prefix(text, open_keys)
            for p in _plate_variants(plate):
                add(p, float(conf), text, bonus)
        for plate, conf, raw in assemble_plates_from_tokens(results):
            if is_junk_text(raw):
                continue
            for p in _plate_variants(plate):
                add(p, conf, raw, bonus)

    def _ranked() -> list[tuple[str, float, str]]:
        return sorted(
            ((p, c, r) for p, (c, r) in best.items()),
            key=lambda x: -x[1],
        )

    def _fast_hit() -> list[tuple[str, float, str]] | None:
        ranked = _ranked()
        if ranked and not _looks_truncated(ranked[0][0]):
            return _drop_false_extra_digit(ranked)
        return None

    if _ALPR is not None:
        try:
            hits = _ALPR.predict(work)
        except Exception:
            hits = []
        dummy = [[0, 0], [1, 0], [1, 1], [0, 1]]
        alpr_crops: list[np.ndarray] = []
        for hit in hits:
            bb = hit.detection.bounding_box
            bw = max(8, bb.x2 - bb.x1)
            bh = max(8, bb.y2 - bb.y1)
            pad_x = max(8, int(bw * 0.10))
            pad_y = max(6, int(bh * 0.14))
            y1, y2 = max(0, bb.y1 - pad_y), min(work.shape[0], bb.y2 + pad_y)
            x1, x2 = max(0, bb.x1 - pad_x), min(work.shape[1], bb.x2 + pad_x)
            trim_r = int((x2 - x1) * 0.08)
            if trim_r > 0:
                x2 = max(x1 + 8, x2 - trim_r)
            crop = work[y1:y2, x1:x2]
            if hit.ocr is not None and hit.ocr.text:
                raw = str(hit.ocr.text).replace("_", "").strip()
                conf = _ocr_mean_conf(hit.ocr.confidence)
                if raw:
                    ingest([(dummy, raw, max(conf, 0.40))], 0.35, bonus=0.30)
            if crop.size >= 80:
                alpr_crops.append(crop)
        quick = _fast_hit()
        if quick:
            return quick
        if _ok():
            for crop in alpr_crops[:2]:
                ingest(_rapid_read(crop), 0.45, bonus=0.20)
                if not _ok():
                    break
        quick = _fast_hit()
        if quick:
            return quick
        if not _ok() and not seated:
            return _drop_false_extra_digit(_ranked())

    h, w = work.shape[:2]
    bumper = work[int(h * 0.58) : int(h * 0.94), int(w * 0.12) : int(w * 0.88)]
    if rear:
        band = work[int(h * 0.08) : int(h * 0.96), int(w * 0.02) : int(w * 0.98)]
    else:
        band = work[int(h * 0.40) : int(h * 0.92), int(w * 0.08) : int(w * 0.92)]

    def _is_333(p: str) -> bool:
        parts = p.split()
        return len(parts) == 3 and len(parts[1]) == 3 and len(parts[2]) == 3

    suspicious_333 = any(
        _is_333(p) and p.split()[2][-1] in {"1", "3", "4", "7"} for p in best
    )
    if _ok() and (suspicious_333 or not best):
        ingest(_rapid_read(bumper), 0.48, bonus=0.16)
        quick = _fast_hit()
        if quick and not suspicious_333:
            return quick
    need_more = (not best) or any(_looks_truncated(p) for p in best)
    if need_more and _ok() and (seated or not best):
        ingest(_rapid_read(band), 0.48, bonus=0.18)
        quick = _fast_hit()
        if quick:
            return quick
        if seated and _ok():
            for crop in find_plate_crops(work, rear=rear):
                if not _ok():
                    break
                rapid_line = _rapid_read(crop)
                ingest(rapid_line, 0.50, bonus=0.10)
                if _fast_hit():
                    break

    return _drop_false_extra_digit(_ranked())


def _drop_false_extra_digit(
    ranked: list[tuple[str, float, str]],
) -> list[tuple[str, float, str]]:
    """16 CAL 75 üzerine vida/ızgaradan 4 eklenmesin (16 CAL 754)."""
    plates = {p for p, _c, _r in ranked}
    drop: set[str] = set()
    for p in plates:
        parts = p.split()
        if len(parts) != 3:
            continue
        city, letters, nums = parts
        if len(letters) != 3 or len(nums) < 3:
            continue
        shorter = f"{city} {letters} {nums[:-1]}"
        if shorter in plates:
            drop.add(p)
    if not drop:
        return ranked
    return [row for row in ranked if row[0] not in drop]


def _three_letter(plate: str) -> bool:
    parts = plate.split()
    return len(parts) == 3 and len(parts[1]) == 3


def read_plates_voted(
    reader, frames: list[np.ndarray], rear: bool = True
) -> list[tuple[str, float, str]]:
    """Birkaç kare oku, ayni plaka tekrar ederse onu seç — tek kare hatasini keser."""
    votes: dict[str, list[tuple[float, str]]] = defaultdict(list)
    last: list[tuple[str, float, str]] = []
    for fr in frames:
        if fr is None:
            continue
        got = read_plates(reader, fr, rear=rear)
        last = got
        for plate, q, raw in got[:4]:
            votes[plate].append((q, raw))
    if not votes:
        return last
    keys = list(votes)
    for short in keys:
        for longer in keys:
            if short == longer or not _is_extension(short, longer):
                continue
            if _three_letter(short):
                continue
            votes[longer].extend(votes[short])
    drop = {
        a
        for a in votes
        if any(_is_extension(a, b) for b in votes if a != b) and not _three_letter(a)
    }
    scored = []
    for plate, hits in votes.items():
        if plate in drop:
            continue
        n = len(hits)
        best_q = max(h[0] for h in hits)
        raw = max(hits, key=lambda h: h[0])[1]
        scored.append((plate, best_q + 0.18 * (n - 1), raw, n, len(plate.replace(" ", ""))))
    scored.sort(key=lambda x: (-x[3], -x[1]))
    ranked = [(p, q, r) for p, q, r, _n, _ln in scored]
    return _drop_false_extra_digit(ranked)


def grab_dwell(url: str, seconds: float = 7.0, n: int = 6) -> list[np.ndarray]:
    """Kamyon kayarken arka plaka gelsin diye aralıklı kare al."""
    cap = open_cam(url, low_latency=False)
    out: list[np.ndarray] = []
    end = time.time() + seconds
    if cap.isOpened():
        grab(cap, tries=10, need=2)
        while time.time() < end and len(out) < n:
            f = grab(cap, tries=5, need=1)
            if f is not None:
                out.append(f)
            time.sleep(0.12)
    try:
        cap.release()
    except Exception:
        pass
    return out


def grab_frames(url: str, n: int = 2) -> list[np.ndarray]:
    """Ana yayından birkaç kare — hareket bulanıklığını azaltır."""
    cap = open_cam(url, low_latency=False)
    out: list[np.ndarray] = []
    if cap.isOpened():
        grab(cap, tries=8, need=2)
        for _ in range(n):
            f = grab(cap, tries=6, need=1)
            if f is not None:
                out.append(f)
            time.sleep(0.08)
    try:
        cap.release()
    except Exception:
        pass
    return out


def _deck_box(cam_id: str) -> tuple[float, float, float, float]:
    for cam in CAMERAS:
        if cam["id"] == cam_id:
            return tuple(cam.get("deck", (0.50, 0.97, 0.12, 0.90)))  # type: ignore[return-value]
    return (0.50, 0.97, 0.12, 0.90)


def deck_view(frame: np.ndarray, cam_id: str = "") -> np.ndarray:
    """Kantar platformu — bayrak, duvar, tepe yok."""
    h, w = frame.shape[:2]
    y0f, y1f, x0f, x1f = _deck_box(cam_id)
    y0, y1 = int(h * y0f), int(h * y1f)
    x0, x1 = int(w * x0f), int(w * x1f)
    y1 = max(y1, y0 + 8)
    x1 = max(x1, x0 + 8)
    roi = frame[y0:y1, x0:x1]
    g = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    return cv2.GaussianBlur(g, (7, 7), 0)


def _empty_path(cam_id: str) -> Path:
    return OUT_DIR / f"empty_{cam_id}.png"


def gray_usable(gray: np.ndarray | None) -> bool:
    """Bos/yanik Neutron karesi zemin referansi olmasin."""
    if gray is None or gray.size < 80:
        return False
    s = float(gray.std())
    m = float(gray.mean())
    return s >= 8.0 and 12.0 <= m <= 245.0


def frame_usable(frame: np.ndarray | None) -> bool:
    if frame is None or getattr(frame, "size", 0) < 80:
        return False
    g = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame
    return gray_usable(g)


def load_empty_ref(cam_id: str) -> np.ndarray | None:
    p = _empty_path(cam_id)
    if not p.is_file():
        return None
    img = cv2.imread(str(p), cv2.IMREAD_GRAYSCALE)
    if not gray_usable(img):
        return None
    return img


def save_empty_ref(cam_id: str, cur: np.ndarray) -> None:
    if not gray_usable(cur):
        return
    try:
        cv2.imwrite(str(_empty_path(cam_id)), cur)
    except Exception:
        pass


def scene_has_truck(frame: np.ndarray) -> bool:
    """Boş zemini beklerken kamyon duruyorsa OCR kaçmasın."""
    return scene_busy(frame) > EMPTY_BUSY


def vehicle_diff(frame: np.ndarray, background: np.ndarray | None) -> float:
    """Boş referansa göre fark — araç gelince yükselir."""
    return vehicle_cover(frame, background)


def _align_light(cur: np.ndarray, bg: np.ndarray) -> np.ndarray:
    """Neutron her açılışta ışık değiştirir; ortalama eşitle."""
    c = cur.astype(np.float32)
    cm = float(c.mean())
    bm = float(bg.mean())
    if cm < 1.0:
        return cur
    return np.clip(c * (bm / cm), 0, 255).astype(np.uint8)


def vehicle_cover(
    frame: np.ndarray, empty_ref: np.ndarray | None, cam_id: str = ""
) -> float:
    """Boş kantar referansına göre araç büyüklüğünde leke (yüzde). Bayrak/boru yok."""
    if empty_ref is None or frame is None:
        return 0.0
    cur = deck_view(frame, cam_id)
    bg = empty_ref
    if cur.shape != bg.shape:
        bg = cv2.resize(bg, (cur.shape[1], cur.shape[0]))
    d = cv2.absdiff(_align_light(cur, bg), bg)
    cut = 48 if cam_id == "165" else 42
    _, th = cv2.threshold(d, cut, 255, cv2.THRESH_BINARY)
    th = cv2.morphologyEx(th, cv2.MORPH_OPEN, np.ones((11, 11), np.uint8))
    th = cv2.morphologyEx(th, cv2.MORPH_CLOSE, np.ones((21, 21), np.uint8))
    n, _, stats, _ = cv2.connectedComponentsWithStats(th)
    if n <= 1:
        return 0.0
    rh, rw = th.shape[:2]
    best = 0.0
    area_n = float(th.size)
    min_w, min_h, min_a = (0.24, 0.18, 0.12) if cam_id == "165" else (0.20, 0.14, 0.08)
    for i in range(1, n):
        bw = int(stats[i, cv2.CC_STAT_WIDTH])
        bh = int(stats[i, cv2.CC_STAT_HEIGHT])
        ba = float(stats[i, cv2.CC_STAT_AREA])
        if bw < rw * min_w or bh < rh * min_h:
            continue
        if ba < area_n * min_a:
            continue
        best = max(best, 100.0 * ba / area_n)
    return best


def deck_occupied(
    frame: np.ndarray, empty_ref: np.ndarray | None, cam_id: str = ""
) -> tuple[float, bool]:
    """Sis/ışık lekesini araç sayma — kamyonda doku da yükselir."""
    cover = vehicle_cover(frame, empty_ref, cam_id)
    need = COVER_MIN.get(cam_id, VEHICLE_COVER)
    if cover < need or empty_ref is None:
        return cover, False
    cur = deck_view(frame, cam_id)
    bg = empty_ref
    if cur.shape != bg.shape:
        bg = cv2.resize(bg, (cur.shape[1], cur.shape[0]))
    tex = float(cur.std()) - float(bg.std())
    return cover, tex >= TEXTURE_MIN.get(cam_id, 3.5)


def write_watch(
    cid: str, frame: np.ndarray | None, cover: float = 0.0, occupied: bool = False
) -> None:
    if frame is None:
        return
    try:
        h, w = frame.shape[:2]
        scale = min(960 / max(w, 1), 540 / max(h, 1), 1.0)
        vis = cv2.resize(
            frame,
            (max(1, int(w * scale)), max(1, int(h * scale))),
            interpolation=cv2.INTER_AREA,
        )
        tag = "ARAC" if occupied else "BOS"
        cv2.putText(
            vis,
            f"{cid} {tag} {cover:.0f}%",
            (16, 36),
            cv2.FONT_HERSHEY_SIMPLEX,
            1.0,
            (0, 255, 0) if occupied else (180, 180, 180),
            2,
        )
        tmp = OUT_DIR / f"watch_{cid}.jpg.tmp"
        cv2.imwrite(str(tmp), vis, [int(cv2.IMWRITE_JPEG_QUALITY), 70])
        tmp.replace(OUT_DIR / f"watch_{cid}.jpg")
    except Exception:
        pass


def presence_allows_ocr(
    ocr_id: str,
    occupied: set[str],
    vehicle_score: dict[str, float],
    last_presence: dict[str, float] | None = None,
    now: float | None = None,
) -> bool:
    """153 giriş ve 165 çıkış kendi karesinden okur."""
    return ocr_id in {"153", "165"}


def main(emit=None, stop=None) -> int:
    """Canlı okuma. emit(kind, **kw) arayüz, stop=Event durdurma."""

    def log(msg: str) -> None:
        if emit:
            emit("log", text=msg)
        else:
            print(msg, flush=True)

    duration = int(os.environ.get("PLAKA_SURE", "0"))
    try:
        cv2.setLogLevel(0)
    except Exception:
        pass
    log("Kamera görüntüsü ayarlanıyor…")
    log(f"  153 {tune_camera_image('172.16.21.153')}")
    log("Plaka motoru yükleniyor…")
    if emit:
        emit("busy", text="Plaka motoru yükleniyor…")
    log(f"  {_init_alpr()}")
    log(f"  {_init_rapid()}")
    reader = None
    if stop is not None and stop.is_set():
        log("Durduruldu.")
        if emit:
            emit("stopped")
        return 0
    if emit:
        emit("busy", text="Kameralara bağlanılıyor…")

    gui = emit is not None
    if not gui:
        test_truck = OUT_DIR / "miss_165_150038.jpg"
        if test_truck.exists():
            got = read_plates(reader, cv2.imread(str(test_truck)))
            log(f"Test (165 eski kamyon): {got[:2] if got else got}")
        for label, path in (
            ("153 Volvo 34CLS224", OUT_DIR / "now_153.jpg"),
            ("153 Renault 16MCD09", OUT_DIR / "153_20260911_170101_17_O_036.jpg"),
            ("153 DAF 43ADU670", OUT_DIR / "153_20260911_171901_31_PU_670.jpg"),
        ):
            if path.exists():
                got = read_plates(reader, cv2.imread(str(path)))
                log(f"Test {label}: {got[:2] if got else got}")

    holds: list[HoldStream | None] = []
    devices: dict[str, DeviceSnap] = {}
    neutrons: dict[str, NeutronSnap] = {}
    last_plate_time: dict[str, float] = {}
    last_miss_time: dict[str, float] = {}
    last_ocr_fail: dict[str, float] = {}
    last_ocr_cam: dict[str, float] = {}
    held_plate: dict[str, str] = {}
    empty_streak: dict[str, int] = {}
    see_on: dict[str, int] = {}
    see_off: dict[str, int] = {}
    empty_ref: dict[str, np.ndarray | None] = {}
    bg_n: dict[str, int] = {}
    for cam in CAMERAS:
        cid, name = cam["id"], cam["name"]
        ip = cam.get("ip")
        if ip:
            ds = DeviceSnap(ip)
            ok = False
            t0 = time.time()
            while time.time() - t0 < 3.0:
                if stop is not None and stop.is_set():
                    break
                if ds.latest() is not None:
                    ok = True
                    break
                time.sleep(0.1)
            if ok:
                devices[cid] = ds
                holds.append(None)
                log(f"{cam_tr(cid)} cihaza bağlandı.")
                if emit:
                    emit("cam", id=cid, name=name, ok=True, mode="device")
            else:
                ds.stop()
                log(f"{cam_tr(cid)} cihaz yanıt vermedi, yayına geçiliyor.")
        if cid in devices:
            pass
        elif cam["hold"]:
            hs = HoldStream(cam["watch"])
            holds.append(hs)
            ok = False
            t0 = time.time()
            while time.time() - t0 < 6.0:
                if stop is not None and stop.is_set():
                    break
                if hs.latest() is not None:
                    ok = True
                    break
                time.sleep(0.2)
            log(
                f"{cam_tr(cid)} bağlandı."
                if ok
                else f"{cam_tr(cid)} bağlanamadı."
            )
            if emit:
                emit("cam", id=cid, name=name, ok=ok, mode="hold")
        else:
            ns = NeutronSnap(cam["watch"])
            neutrons[cid] = ns
            log(f"{cam_tr(cid)} arka plan karesi.")
            if emit:
                emit("cam", id=cid, name=name, ok=True, mode="brief")
            holds.append(None)
        loaded = load_empty_ref(cid)
        empty_ref[cid] = loaded
        bg_n[cid] = BG_WARMUP if loaded is not None else 0
        empty_streak[cid] = 0
        see_on[cid] = 0
        see_off[cid] = 0
        if loaded is not None:
            log(f"{cam_tr(cid)}: kayıtlı boş zemin yüklendi.")

    detections: dict[str, list] = defaultdict(list)
    irsaliye_idx = IrsaliyeIndex()
    n_irs = irsaliye_idx.refresh(force=True)
    log(f"e-İrsaliye: {n_irs} belge · {len(irsaliye_idx.by_plate)} plaka")

    def _eportal_loop() -> None:
        missing_logged = False
        while stop is None or not stop.is_set():
            pcfg = load_eportal_config()
            has_dp = pcfg.get("dp_login") and pcfg.get("dp_password") and pcfg.get("dp_corporate")
            if has_dp:
                missing_logged = False
                try:
                    info = sync_eportal(log=log, cfg=pcfg)
                    irsaliye_idx.refresh(force=True)
                    log(
                        f"Digital Planet: {info.get('total', 0)} belge, "
                        f"{info.get('added', 0)} yeni"
                    )
                except Exception as exc:
                    log(f"e-irsaliye: {exc}")
            elif not missing_logged:
                log("e-irsaliye: Digital Planet ayarı yok.")
                missing_logged = True
            for _ in range(180):
                if stop is not None and stop.is_set():
                    return
                time.sleep(1.0)

    threading.Thread(target=_eportal_loop, daemon=True).start()
    start = time.time()
    log("İzleme başladı. Plaka kantara binmeden okunur.")
    try:
        for cid, title in (("153", "Giriş Kil kapısı"), ("165", "Çıkış KNT_KOMHMJ")):
            w0 = live_weight(cid)
            if w0:
                log(f"{title} bağlı: {w0.get('name')} · anlık {format_kg(w0.get('kg'))}")
            else:
                log(f"{title} ağırlığı okunamadı (IFS).")
    except Exception as exc:
        log(f"Kantar IFS: {exc}")
    if emit:
        emit("ready")
    else:
        log("Durdurmak: Ctrl+C")

    round_n = 0
    last_status_sig: tuple = ()
    last_logged_empty = True
    last_presence: dict[str, float] = {}
    neutron_last: dict[str, float] = {}
    last_neutron_frame: dict[str, np.ndarray] = {}
    prev_occupied: set[str] = set()
    idx_by_id = {c["id"]: i for i, c in enumerate(CAMERAS)}
    yard = ScaleYard()
    last_scale_poll = {"153": 0.0, "165": 0.0}
    live_kg: dict | None = None

    def _open_keys() -> set[str]:
        try:
            return get_store().open_plate_keys()
        except Exception as exc:
            log(f"Visit açık plaka: {exc}")
            return set()

    def _has_irs(plate: str) -> bool:
        try:
            return bool(irsaliye_idx.has_plate(plate))
        except Exception:
            return False

    for _lane in yard.lanes.values():
        _lane.open_visit_keys = _open_keys
        _lane.has_irsaliye = _has_irs

    def _emit_scale(pid: str, sound: str | None = None) -> None:
        snap = yard.lane(pid).snapshot()
        seated = snap.state in {
            ScaleState.WEIGHT_STABLE,
            ScaleState.WEIGHT_CAPTURED,
            ScaleState.WAITING_SCALE_CLEAR,
        }
        empty = snap.state == ScaleState.SCALE_EMPTY
        if snap.state == ScaleState.COMM_ERROR:
            st = COMMUNICATION_ERROR
        elif seated and snap.captured:
            st = "STABLE"
        elif empty:
            st = "NO_WEIGHT"
        else:
            st = "UNSTABLE"
        if emit:
            emit(
                "scale",
                id=pid,
                state=snap.state,
                kg=snap.kg,
                samples=snap.samples,
                candidates=snap.candidates,
                selected=snap.selected,
                captured=snap.captured,
                reason=snap.reason,
                cycle_id=snap.cycle_id,
                direction=snap.direction,
                debug=snap.debug,
                timeline=snap.timeline,
                sound=sound,
            )
            emit(
                "weight",
                id=pid,
                kg=snap.kg,
                ts=datetime.now().strftime("%H:%M:%S"),
                name=pid,
                seated=seated and snap.captured,
                empty=empty,
                status=st,
                scale_gate=(
                    "WAITING_SCALE_CLEAR"
                    if snap.state == ScaleState.WAITING_SCALE_CLEAR
                    else snap.state
                ),
                scale_state=snap.state,
            )

    def _handle_scale(pid: str, actions: list) -> None:
        nonlocal live_kg
        sounds = [a.sound for a in actions if getattr(a, "sound", None)]
        sound = sounds[-1] if sounds else None
        for act in actions:
            if act.kind not in {"capture", "review"}:
                continue
            try:
                store = get_store()
                visit = None
                if act.kind == "capture" and act.plate:
                    if pid == "153":
                        res = store.on_entry(
                            plate=act.plate,
                            raw_plate=act.raw_plate or act.plate,
                            camera="153",
                            full_weight=act.kg,
                        )
                    else:
                        res = store.on_exit(
                            plate=act.plate,
                            raw_plate=act.raw_plate or act.plate,
                            camera="165",
                            empty_weight=act.kg,
                        )
                    visit = res.get("visit")
                    extra = f" · {res.get('reason')}" if res.get("reason") else ""
                    if visit:
                        log(
                            f"Visit {res.get('action')}: {visit.get('visit_id')} "
                            f"{visit.get('plate')} {visit.get('status')}{extra}"
                        )
                elif act.kind == "review":
                    visit = store.capture_unidentified(
                        camera=pid,
                        kg=float(act.kg or 0),
                        cycle_id=act.cycle_id,
                        reason=str(act.reason or "MANUAL_REVIEW"),
                        plate=act.plate,
                        raw_plate=act.raw_plate,
                        candidates=act.candidates,
                        timeline=act.timeline,
                    )
                    log(
                        f"Visit review: {visit.get('visit_id')} {act.reason} "
                        f"{format_kg(act.kg)}"
                    )
                if visit:
                    if act.kind == "capture":
                        store.tag_cycle(visit["visit_id"], act.cycle_id, act.timeline)
                    if emit:
                        emit("visit", visit=visit, sound=act.sound)
            except Exception as exc:
                log(f"Visit store: {exc}")
        _emit_scale(pid, sound=sound)
        snap = yard.lane(pid).snapshot()
        if snap.kg is not None:
            live_kg = {"kg": snap.kg, "cam_id": pid, "empty": snap.state == ScaleState.SCALE_EMPTY}

    def _ocr_one_camera(cid: str, name: str, frame, diff: float, ts: str) -> None:
        if frame is not None:
            _sh, lens = lens_health(frame)
            if lens != "ok":
                log(
                    f"{cam_tr(cid)}: görüntü {lens}, yazılım netleştirmeyi deniyor."
                )
        cam = by_id[cid]
        if cam.get("role") != "ocr":
            return
        if cid == "165":
            cached = last_neutron_frame.get(cid)
            if cached is not None:
                frame = cached
        if cid == "153" and (frame is None or (devices.get(cid) and devices[cid].age() > 1.2)):
            fresh = dahua_snapshot(str(cam.get("ip") or "172.16.21.153"), 0.55)
            if fresh is not None:
                frame = fresh
        frame = shrink_ocr_frame(frame)
        seq: list[np.ndarray] = []
        plates: list = []
        ocr_frame = frame
        if frame is not None:
            seq.append(frame)
            lane = yard.lane(cid)
            seated = lane.state not in {
                ScaleState.SCALE_EMPTY,
                ScaleState.WAITING_SCALE_CLEAR,
                ScaleState.WEIGHT_CAPTURED,
            }
            peer_need = False
            if cid == "165":
                other = yard.lane("153")
                peer_need = (
                    other.want_ocr()
                    and other.state != ScaleState.SCALE_EMPTY
                )
            deadline = 0.55 if peer_need else (1.55 if seated else 0.90)
            plates = read_plates(
                reader,
                frame,
                rear=True,
                open_keys=_open_keys(),
                seated=seated and not peer_need,
                deadline=deadline,
            )
        if ocr_frame is None and seq:
            ocr_frame = seq[-1]
        if ocr_frame is None:
            log(f"{cam_tr(cid)}: kare alınamadı.")
            return
        if not plates:
            last_ocr_fail[cid] = time.time()
            if cid == "165":
                log(f"{cam_tr(cid)}: plaka henüz karede yok, arka tampon bekleniyor.")
            else:
                log(f"{cam_tr(cid)}: plaka yok.")
            now_m = time.time()
            if now_m - last_miss_time.get(cid, 0) >= 90:
                last_miss_time[cid] = now_m
                cv2.imwrite(
                    str(OUT_DIR / f"miss_{cid}_{datetime.now():%H%M%S}.jpg"),
                    ocr_frame,
                )
            return
        top = plates[0]
        try:
            full = irsaliye_idx.complete_plate(top[0])
        except Exception as exc:
            log(f"Plaka tamamla: {exc}")
            full = None
        if not full:
            try:
                k = plate_key(top[0])
                cands = [
                    p
                    for p in get_store().open_plate_keys()
                    if plate_extends(k, p)
                ]
                if len(cands) == 1:
                    full = format_plate(cands[0])
            except Exception as exc:
                log(f"Açık plaka tamamla: {exc}")
                full = None
        if full and full != top[0]:
            log(f"{cam_tr(cid)}: {top[0]} → {full} (e-irsaliye)")
            top = (full, float(top[1]) + 0.25, top[2])
        now = time.time()
        fname = (
            OUT_DIR
            / f"{cid}_{datetime.now():%Y%m%d_%H%M%S}_{top[0].replace(' ', '_')}.jpg"
        )
        try:
            ann = ocr_frame.copy()
            cv2.putText(
                ann,
                top[0],
                (40, 70),
                cv2.FONT_HERSHEY_SIMPLEX,
                1.8,
                (0, 255, 0),
                3,
            )
            cv2.imwrite(str(fname), ann)
        except Exception as exc:
            log(f"OCR kare kaydı: {exc}")
            fname = Path("")
        try:
            acts = yard.lane(cid).on_plate(
                plate=top[0],
                raw=str(top[2] or ""),
                confidence=top[1],
                image_path=str(fname) if fname else "",
                now=now,
            )
            _handle_scale(cid, acts)
        except Exception as exc:
            log(f"Scale plate: {exc}")
        prev = held_plate.get(cid, "")
        ui_skip = False
        if prev and _same_visit(prev, top[0]):
            ui_skip = True
        elif prev and _is_extension(top[0], prev) and not _three_letter(top[0]):
            ui_skip = True
        elif prev and _is_extension(prev, top[0]) and _three_letter(prev):
            ui_skip = True
        key = f"{cid}:{top[0]}"
        if now - last_plate_time.get(key, 0) < DEDUP_SEC:
            held_plate[cid] = top[0]
            ui_skip = True
        if ui_skip:
            return
        held_plate[cid] = top[0]
        last_plate_time[key] = now
        detections[cid].append(
            {
                "time": ts,
                "diff": diff,
                "plates": [
                    {"text": p, "conf": c, "raw": r} for p, c, r in plates[:5]
                ],
                "file": str(fname),
                "irsaliye": "",
            }
        )
        snap = yard.lane(cid).snapshot()
        log(f"{cam_tr(cid)} plaka: {top[0]}")
        if emit:
            emit(
                "plate",
                id=cid,
                name=cam_tr(cid),
                plate=top[0],
                conf=top[1],
                raw=top[2],
                file=str(fname),
                ts=ts,
                irsaliye=None,
                irsaliyeler=[],
                weight=snap.kg,
                weight_seated=snap.captured,
                scale_state=snap.state,
            )
        when = datetime.now()
        try:
            hits = irsaliye_idx.docs_for_when(top[0], when)
        except Exception as exc:
            log(f"İrsaliye eşle: {exc}")
            hits = []
        if hits:
            log(
                f"{cam_tr(cid)}: {len(hits)} irsaliye — "
                f"çıkıştan önceki saate göre, kantarcı seçsin"
            )
            if emit:
                emit(
                    "plate",
                    id=cid,
                    name=cam_tr(cid),
                    plate=top[0],
                    conf=top[1],
                    raw=top[2],
                    file=str(fname),
                    ts=ts,
                    irsaliye=None,
                    irsaliyeler=hits,
                    weight=snap.kg,
                    weight_seated=snap.captured,
                    scale_state=snap.state,
                )
        else:
            log(f"{cam_tr(cid)}: irsaliye yok — numara yazılsın")

    try:
        while (stop is None or not stop.is_set()) and (
            duration <= 0 or (time.time() - start < duration)
        ):
            round_n += 1
            ts = datetime.now().strftime("%H:%M:%S")
            frames: dict[str, np.ndarray] = {}
            idle_skip: set[str] = set()
            neutron_fresh: set[str] = set()

            # 1) Dahua cihaz HTTP; yoksa alt yayin. Neutron kisa kare
            for idx, cam in enumerate(CAMERAS):
                cid = cam["id"]
                dev = devices.get(cid)
                if dev is not None:
                    frame = dev.latest()
                    if frame is not None and frame_usable(frame):
                        frames[cid] = frame
                    continue
                if cam["hold"]:
                    hs = holds[idx]
                    if hs is None:
                        continue
                    frame = hs.latest()
                    if frame is None or not frame_usable(frame):
                        continue
                    frames[cid] = frame
                    continue
                ns = neutrons.get(cid)
                if ns is None:
                    continue
                scale_busy = False
                try:
                    scale_busy = (
                        yard.lane(cid).want_ocr()
                        and yard.lane(cid).state != ScaleState.SCALE_EMPTY
                    )
                except Exception:
                    scale_busy = False
                ns.set_busy(cid in prev_occupied or scale_busy)
                frame = ns.latest()
                if frame is not None and frame_usable(frame):
                    frames[cid] = frame
                    last_neutron_frame[cid] = frame
                    fresh_age = 3.0 if (cid in prev_occupied or scale_busy) else 5.0
                    if ns.is_fresh(fresh_age):
                        neutron_fresh.add(cid)
                elif cid in last_neutron_frame:
                    frames[cid] = last_neutron_frame[cid]

            # 2) Araç var/yok: her kamera kendi karesinden (153 giriş, 165 çıkış)
            vehicle_score: dict[str, float] = {}
            cams_state: list[dict] = []
            by_id = {c["id"]: c for c in CAMERAS}
            for cam in CAMERAS:
                cid, name = cam["id"], cam["name"]
                if cid not in frames:
                    see_on[cid] = 0
                    see_off[cid] = see_off.get(cid, 0) + 1
                    if cid in idle_skip:
                        cams_state.append({"id": cid, "state": "empty", "text": "Boş"})
                    else:
                        state = "busy" if not cam["hold"] else "down"
                        text = "Meşgul" if state == "busy" else "Görüntü yok"
                        cams_state.append({"id": cid, "state": state, "text": text})
                    continue
                frame = frames[cid]
                if cid == "165" and cid not in neutron_fresh:
                    if cid in prev_occupied:
                        cams_state.append(
                            {"id": cid, "state": "vehicle", "text": "Araç var"}
                        )
                        vehicle_score[cid] = COVER_MIN.get(cid, VEHICLE_COVER)
                    else:
                        cams_state.append({"id": cid, "state": "empty", "text": "Boş"})
                    write_watch(cid, frame, 0.0, cid in prev_occupied)
                    continue
                cur = deck_view(frame, cid)
                ref = empty_ref[cid]
                need = COVER_MIN.get(cid, VEHICLE_COVER)
                if ref is None or bg_n[cid] < BG_WARMUP:
                    probe, hit = (
                        deck_occupied(frame, ref, cid)
                        if ref is not None
                        else (0.0, False)
                    )
                    if hit:
                        see_on[cid] = see_on.get(cid, 0) + 1
                        see_off[cid] = 0
                        cams_state.append(
                            {"id": cid, "state": "vehicle", "text": "Araç var"}
                        )
                        vehicle_score[cid] = max(probe, need)
                        write_watch(cid, frame, probe, True)
                        continue
                    if gray_usable(cur):
                        if ref is None or cur.shape != ref.shape:
                            empty_ref[cid] = cur
                        else:
                            empty_ref[cid] = cv2.addWeighted(ref, 0.55, cur, 0.45, 0)
                        bg_n[cid] += 1
                    see_on[cid] = 0
                    if bg_n[cid] < BG_WARMUP:
                        cams_state.append(
                            {
                                "id": cid,
                                "state": "warmup",
                                "text": f"Zemin {bg_n[cid]}/{BG_WARMUP}",
                                "n": bg_n[cid],
                            }
                        )
                        write_watch(cid, frame, probe, False)
                        continue
                    save_empty_ref(cid, empty_ref[cid])

                cover, occupied = deck_occupied(frame, empty_ref[cid], cid)
                was = cid in prev_occupied
                if occupied:
                    see_on[cid] = see_on.get(cid, 0) + 1
                    see_off[cid] = 0
                else:
                    see_off[cid] = see_off.get(cid, 0) + 1
                    see_on[cid] = 0
                if was:
                    seen = see_off.get(cid, 0) < PRESENCE_OFF
                else:
                    seen = see_on.get(cid, 0) >= PRESENCE_ON
                if not seen:
                    if not occupied and empty_ref[cid] is not None and gray_usable(cur):
                        if cur.shape == empty_ref[cid].shape:
                            empty_ref[cid] = cv2.addWeighted(
                                empty_ref[cid], 0.88, cur, 0.12, 0
                            )
                        else:
                            empty_ref[cid] = cur
                        save_empty_ref(cid, empty_ref[cid])
                    cams_state.append({"id": cid, "state": "empty", "text": "Boş"})
                    write_watch(cid, frame, cover, False)
                    continue

                cams_state.append({"id": cid, "state": "vehicle", "text": "Araç var"})
                vehicle_score[cid] = max(cover, need)
                write_watch(cid, frame, cover, True)

            occupied_now = {c["id"] for c in cams_state if c["state"] == "vehicle"}
            now_p = time.time()
            for pid, cover in vehicle_score.items():
                if by_id[pid]["role"] == "presence":
                    last_presence[pid] = now_p
            for st in cams_state:
                oid = st["id"]
                if by_id[oid]["role"] != "ocr" or st["state"] != "vehicle":
                    continue
                if presence_allows_ocr(
                    oid, occupied_now, vehicle_score, last_presence, now_p
                ):
                    continue
                st["state"] = "empty"
                st["text"] = "Boş"
                vehicle_score.pop(oid, None)

            occupied = {c["id"] for c in cams_state if c["state"] == "vehicle"}
            prev_occupied = set(occupied)
            for cam in CAMERAS:
                if cam["role"] != "ocr":
                    continue
                oid = cam["id"]
                pair_ids = [c["id"] for c in CAMERAS if c["ocr_id"] == oid]
                lane_busy = any(pid in occupied for pid in pair_ids)
                if lane_busy:
                    empty_streak[oid] = 0
                else:
                    empty_streak[oid] = empty_streak.get(oid, 0) + 1
                    if empty_streak[oid] >= EMPTY_CLEAR_ROUNDS and oid in held_plate:
                        log(f"{cam_tr(oid)}: araç gitti, {held_plate[oid]} kapatıldı.")
                        held_plate.pop(oid, None)
                    if yard.lane(oid).state == ScaleState.SCALE_EMPTY:
                        held_plate.pop(oid, None)

            ocr_score: dict[str, float] = {}
            for vid, dscore in vehicle_score.items():
                if by_id[vid]["role"] == "ocr":
                    ocr_score[vid] = max(ocr_score.get(vid, 0.0), dscore)
            for oid in ("153", "165"):
                lane = yard.lane(oid)
                if not lane.want_ocr():
                    continue
                if lane.state in {
                    ScaleState.VEHICLE_ENTERING,
                    ScaleState.WEIGHT_RISING,
                    ScaleState.WEIGHT_STABILIZING,
                    ScaleState.WEIGHT_STABLE,
                    ScaleState.PLATE_CANDIDATE,
                    ScaleState.COMM_ERROR,
                    ScaleState.RECOVERY,
                } and oid not in ocr_score:
                    ocr_score[oid] = COVER_MIN.get(oid, VEHICLE_COVER)
            candidates: list[tuple[str, str, np.ndarray | None, float]] = []
            for oid, dscore in ocr_score.items():
                if held_plate.get(oid) and not yard.lane(oid).want_ocr():
                    continue
                if held_plate.get(oid) and now_p - last_ocr_cam.get(oid, 0) < 0.55:
                    continue
                if now_p - last_ocr_fail.get(oid, 0) < 0.18:
                    continue
                scale_need = yard.lane(oid).want_ocr() and yard.lane(oid).state != ScaleState.SCALE_EMPTY
                if dscore < COVER_MIN.get(oid, VEHICLE_COVER) and not scale_need:
                    continue
                ocam = by_id[oid]
                fr = frames.get(oid)
                dev = devices.get(oid)
                if dev is not None:
                    fresh = dev.latest()
                    if fresh is not None:
                        fr = fresh
                        frames[oid] = fresh
                else:
                    idx = idx_by_id.get(oid)
                    if ocam["hold"] and idx is not None and holds[idx] is not None:
                        fresh = holds[idx].latest()
                        if fresh is not None:
                            fr = fresh
                            frames[oid] = fresh
                candidates.append((oid, ocam["name"], fr, dscore))
            candidates.sort(key=lambda row: 0 if row[0] == "153" else 1)

            summary = _status_summary(cams_state)
            sig = tuple((c["id"], c["state"]) for c in cams_state)
            if emit:
                emit("status", ts=ts, cams=cams_state, summary=summary)
            if sig != last_status_sig:
                last_status_sig = sig
                interesting = any(
                    c["state"] in {"vehicle", "warmup", "down", "busy"} for c in cams_state
                )
                if interesting:
                    log(summary)
                    last_logged_empty = False
                elif not last_logged_empty:
                    log(summary)
                    last_logged_empty = True

            # 3) Plaka önce — kantar sorgusu okumayı bekletmesin
            for cid, name, frame, diff in candidates:
                last_ocr_cam[cid] = time.time()
                show_ocr = not bool(held_plate.get(cid))
                if show_ocr:
                    log(f"{cam_tr(cid)}: plaka okunuyor…")
                    if emit:
                        emit("ocr", id=cid, name=name, running=True)
                try:
                    _ocr_one_camera(cid, name, frame, diff, ts)
                except Exception as exc:
                    log(f"{cam_tr(cid)} okuma hatası: {exc}")
                finally:
                    if show_ocr and emit:
                        emit("ocr", id=cid, name=name, running=False)

            now_p = time.time()
            for oid in ("153", "165"):
                lane = yard.lane(oid)
                busy = lane.needs_poll() or oid in occupied
                if not (busy or (now_p - last_scale_poll.get(oid, 0) >= 1.0)):
                    continue
                last_scale_poll[oid] = now_p
                snap = live_weight(oid)
                try:
                    acts = lane.on_weight(snap, now_p)
                    acts += lane.tick(now_p)
                    _handle_scale(oid, acts)
                except Exception as exc:
                    log(f"{oid} kantar: {exc}")

            wait = 0.22 if not occupied else 0.04
            if round_n % 20 == 0:
                try:
                    irsaliye_idx.refresh()
                except Exception as exc:
                    log(f"İrsaliye indeks: {exc}")
            waited = 0.0
            while waited < wait:
                if stop is not None and stop.is_set():
                    break
                step = min(0.2, wait - waited)
                time.sleep(step)
                waited += step

    except KeyboardInterrupt:
        log("Durduruldu (Ctrl+C).")
    except Exception as exc:
        log(f"Okuma döngüsü: {exc}")

    for h in holds:
        if h is not None:
            h.stop()
    for ds in devices.values():
        ds.stop()
    for ns in neutrons.values():
        ns.stop()

    summary = {
        "ended": datetime.now().isoformat(timespec="seconds"),
        "rounds": round_n,
        "detections": dict(detections),
        "unique": {
            k: sorted({p["text"] for d in v for p in d["plates"]})
            for k, v in detections.items()
        },
    }
    (OUT_DIR / "live_report.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    log("=== OZET ===")
    for cam in CAMERAS:
        log(
            f"  {cam_tr(cam['id'])}: {summary['unique'].get(cam['id'], []) or 'plaka yok'}"
        )
    log(f"Rapor: {OUT_DIR / 'live_report.json'}")
    if emit:
        emit("stopped")
    return 0


if __name__ == "__main__":
    emit = None
    if os.environ.get("PLAKA_GUI") == "1":
        def emit(kind: str, **kw) -> None:  # type: ignore[misc]
            try:
                print(
                    "@@" + json.dumps({"kind": kind, **kw}, ensure_ascii=False, default=str),
                    flush=True,
                )
            except Exception as exc:
                print(f"@@{json.dumps({'kind': 'log', 'text': f'emit {kind}: {exc}'})}", flush=True)

    raise SystemExit(main(emit=emit))
