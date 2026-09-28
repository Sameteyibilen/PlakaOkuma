#!/usr/bin/env python3
"""Edge'de 4 kamera — kameranin kendi eklentisi gerekmez."""

from __future__ import annotations

import os
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import cv2

os.environ.setdefault("TEMP", r"D:\PlakaOkuma\.tmp")
os.environ.setdefault("TMP", r"D:\PlakaOkuma\.tmp")
os.environ.setdefault("EASYOCR_MODULE_PATH", r"D:\PlakaOkuma\.EasyOCR")

from izle_kameralar import CAMS, grab_brief  # noqa: E402

HOST, PORT = "127.0.0.1", 8765
USER, PASS = "admin", "admin"
OUT = Path(__file__).resolve().parent / "live_ocr"
OUT.mkdir(parents=True, exist_ok=True)

# Dahua HTTP karesi RTSP'yi mesgul etmez. Neutron: kisa RTSP.
SNAP_HTTP = {
    "152": "http://172.16.21.152/cgi-bin/snapshot.cgi",
    "153": "http://172.16.21.153/cgi-bin/snapshot.cgi",
}

_lock = threading.Lock()
_jpeg: dict[str, bytes] = {}
_ok: dict[str, bool] = {c["id"]: False for c in CAMS}
_stop = threading.Event()


def _log(msg: str) -> None:
    line = time.strftime("%H:%M:%S ") + msg + "\n"
    try:
        with (OUT / "web_izle.log").open("a", encoding="utf-8") as f:
            f.write(line)
    except OSError:
        pass


def _auth_snap(url: str) -> bytes | None:
    req = urllib.request.Request(url)
    token = (USER + ":" + PASS).encode()
    import base64

    req.add_header("Authorization", "Basic " + base64.b64encode(token).decode())
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = resp.read()
            if data[:2] == b"\xff\xd8":
                return data
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        _log(f"snap fail {url}: {type(e).__name__}")
    return None


def _ocr_watch_jpeg(cid: str) -> bytes | None:
    """OCR zaten 165/153 çekiyorsa ikinci oturum açma."""
    path = OUT / f"watch_{cid}.jpg"
    try:
        if not path.is_file():
            return None
        if time.time() - path.stat().st_mtime > 8:
            return None
        data = path.read_bytes()
    except OSError:
        return None
    return data if data[:2] == b"\xff\xd8" else None


_ocr_chk = 0.0
_ocr_on = False


def _ocr_running() -> bool:
    global _ocr_chk, _ocr_on
    now = time.time()
    if now - _ocr_chk < 12:
        return _ocr_on
    _ocr_chk = now
    watch = OUT / "watch_165.jpg"
    try:
        if watch.is_file() and now - watch.stat().st_mtime < 20:
            _ocr_on = True
            return True
    except OSError:
        pass
    try:
        import subprocess

        out = subprocess.run(
            ["wmic", "process", "where", "CommandLine like '%live_plate_ocr.py%'", "get", "ProcessId"],
            capture_output=True,
            text=True,
            timeout=4,
        )
        _ocr_on = "live_plate" in (out.stdout or "") or any(
            x.strip().isdigit() for x in (out.stdout or "").splitlines()[1:]
        )
    except Exception:
        _ocr_on = False
    return _ocr_on


def _rtsp_jpeg(url: str) -> bytes | None:
    frame = grab_brief(url)
    if frame is None:
        return None
    ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
    return buf.tobytes() if ok else None


def _updater() -> None:
    last_n = {"152": 0.0, "164": 0.0, "165": 0.0}
    while not _stop.is_set():
        ocr_on = _ocr_running()
        for cam in CAMS:
            cid = cam["id"]
            data = None
            watched = _ocr_watch_jpeg(cid)
            if watched:
                data = watched
            elif cid in SNAP_HTTP:
                data = _auth_snap(SNAP_HTTP[cid])
            else:
                now = time.time()
                gap = 5.0 if cid == "165" else 4.0
                if now - last_n.get(cid, 0) < gap:
                    continue
                last_n[cid] = now
                if cid == "165" and ocr_on:
                    continue
                data = _rtsp_jpeg(cam["url"])
            if data:
                with _lock:
                    _jpeg[cid] = data
                    _ok[cid] = True
            elif not watched:
                with _lock:
                    _ok[cid] = False
        _stop.wait(0.7)


PAGE = """<!DOCTYPE html>
<html lang="tr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Plaka Okuma — Kameralar</title>
<style>
  * { box-sizing: border-box; }
  body { margin: 0; font-family: Segoe UI, Arial, sans-serif; background: #111; color: #eee; }
  header { padding: 14px 20px; background: #1c1c1c; border-bottom: 1px solid #333;
           display: flex; justify-content: space-between; align-items: center; }
  h1 { margin: 0; font-size: 20px; font-weight: 600; }
  .note { color: #aaa; font-size: 13px; }
  .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; padding: 8px; }
  .cell { background: #000; position: relative; min-height: 240px; }
  .cell img { width: 100%; height: auto; display: block; background: #222; }
  .lbl { position: absolute; left: 0; top: 0; padding: 6px 10px; background: rgba(0,0,0,.75);
         font-size: 15px; }
  .on { color: #7d7; } .off { color: #f66; }
</style>
</head>
<body>
<header>
  <h1>Kantarma kameraları</h1>
  <div class="note">Kamera adresi (152 / 165) tarayıcıda açılmaz. Bu sayfa eklentisiz çalışır. 165 tek oturum — uygulama açıkken onun karesi kullanılır.</div>
</header>
<div class="grid">
  <div class="cell"><div class="lbl" id="l152">152 Giriş ön</div><img id="i152" alt="152"></div>
  <div class="cell"><div class="lbl" id="l153">153 Giriş arka</div><img id="i153" alt="153"></div>
  <div class="cell"><div class="lbl" id="l164">164 Çıkış ön</div><img id="i164" alt="164"></div>
  <div class="cell"><div class="lbl" id="l165">165 Çıkış arka</div><img id="i165" alt="165"></div>
</div>
<script>
const ids = ["152","153","164","165"];
function tick() {
  const t = Date.now();
  ids.forEach(id => {
    const img = document.getElementById("i"+id);
    img.src = "/cam/"+id+".jpg?t="+t;
  });
}
tick();
setInterval(tick, 900);
</script>
</body>
</html>
"""

PLACEHOLDER = None


def _placeholder() -> bytes:
    global PLACEHOLDER
    if PLACEHOLDER:
        return PLACEHOLDER
    import numpy as np

    img = np.zeros((360, 640, 3), dtype=np.uint8)
    cv2.putText(img, "baglanti yok", (160, 190), cv2.FONT_HERSHEY_SIMPLEX, 1.1, (0, 0, 255), 2)
    ok, buf = cv2.imencode(".jpg", img)
    PLACEHOLDER = buf.tobytes() if ok else b""
    return PLACEHOLDER


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:
        return

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            body = PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path.startswith("/cam/") and path.endswith(".jpg"):
            cid = path[5:-4]
            with _lock:
                data = _jpeg.get(cid)
            if not data:
                data = _placeholder()
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        self.send_error(404)


def main() -> int:
    try:
        cv2.setLogLevel(0)
    except Exception:
        pass
    t = threading.Thread(target=_updater, name="cams", daemon=True)
    t.start()
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    _log(f"http://{HOST}:{PORT}")
    print(f"Tarayici: http://{HOST}:{PORT}", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        _stop.set()
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
