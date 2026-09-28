#!/usr/bin/env python3
"""4 kamera RTSP bağlantı testi — tek kare alır, plaka OCR yok."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

OUT_DIR = Path(__file__).resolve().parent / "snapshots"
OUT_DIR.mkdir(parents=True, exist_ok=True)

USER = "admin"
PASS = "admin"

CAMERAS = [
    {
        "id": "152",
        "name": "Giris kantar on",
        "ip": "172.16.21.152",
        "kind": "dahua",
    },
    {
        "id": "153",
        "name": "Giris kantar arka",
        "ip": "172.16.21.153",
        "kind": "dahua",
    },
    {
        "id": "164",
        "name": "Cikis kantar on",
        "ip": "172.16.21.164",
        "kind": "oem",
    },
    {
        "id": "165",
        "name": "Cikis kantar arka",
        "ip": "172.16.21.165",
        "kind": "oem",
    },
]


def dahua_urls(ip: str) -> list[str]:
    base = f"rtsp://{USER}:{PASS}@{ip}:554"
    return [
        f"{base}/cam/realmonitor?channel=1&subtype=0",
        f"{base}/cam/realmonitor?channel=1&subtype=1",
        f"{base}/cam/realmonitor?channel=1&subtype=2",
    ]


def oem_urls(ip: str) -> list[str]:
    base = f"rtsp://{USER}:{PASS}@{ip}:554"
    return [
        f"{base}/media/video1",
        f"{base}/media/video2",
        f"{base}/stream1",
        f"{base}/stream0",
        f"{base}/h264",
        f"{base}/live",
        f"{base}/live/ch0",
        f"{base}/live/ch00_0",
        f"{base}/Streaming/Channels/101",
        f"{base}/Streaming/Channels/1",
        f"{base}/user={USER}&password={PASS}&channel=1&stream=0.sdp",
        f"{base}/user={USER}&password={PASS}&channel=1&stream=1.sdp",
        f"{base}/videoMain",
        f"{base}/video",
        f"{base}/ch0_0.h264",
        f"{base}/ch1/main/av_stream",
        f"{base}/",
    ]


def onvif_get_stream_uri(ip: str) -> str | None:
    """ONVIF GetStreamUri — Digest auth denemesi + Basic."""
    body = """<?xml version="1.0" encoding="UTF-8"?>
<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"
 xmlns:trt="http://www.onvif.org/ver10/media/wsdl"
 xmlns:tt="http://www.onvif.org/ver10/schema">
  <s:Body>
    <trt:GetStreamUri>
      <trt:StreamSetup>
        <tt:Stream>RTP-Unicast</tt:Stream>
        <tt:Transport><tt:Protocol>RTSP</tt:Protocol></tt:Transport>
      </trt:StreamSetup>
      <trt:ProfileToken>Profile_1</trt:ProfileToken>
    </trt:GetStreamUri>
  </s:Body>
</s:Envelope>"""

    endpoints = [
        f"http://{ip}/onvif/device_service",
        f"http://{ip}/onvif/media_service",
        f"http://{ip}/onvif/Media",
        f"http://{ip}:80/onvif/device_service",
    ]

    # Try GetProfiles first for a real token
    profiles_body = """<?xml version="1.0" encoding="UTF-8"?>
<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"
 xmlns:trt="http://www.onvif.org/ver10/media/wsdl">
  <s:Body><trt:GetProfiles/></s:Body>
</s:Envelope>"""

    tokens: list[str] = ["Profile_1", "profile1", "MediaProfile000", "Profile000"]

    for ep in endpoints:
        xml = soap_post(ep, profiles_body)
        if xml:
            try:
                root = ET.fromstring(xml)
                for el in root.iter():
                    if el.tag.endswith("Profiles") or el.tag.endswith("Profile"):
                        tok = el.attrib.get("token")
                        if tok and tok not in tokens:
                            tokens.insert(0, tok)
            except ET.ParseError:
                pass

    for ep in endpoints:
        for token in tokens[:6]:
            req_body = body.replace("Profile_1", token)
            xml = soap_post(ep, req_body)
            if not xml:
                continue
            try:
                root = ET.fromstring(xml)
                for el in root.iter():
                    if el.tag.endswith("Uri") and el.text and el.text.startswith("rtsp"):
                        uri = el.text
                        # inject credentials if missing
                        if f"{USER}:" not in uri:
                            uri = uri.replace("rtsp://", f"rtsp://{USER}:{PASS}@", 1)
                        return uri
            except ET.ParseError:
                continue
    return None


def soap_post(url: str, body: str) -> str | None:
    req = urllib.request.Request(
        url,
        data=body.encode("utf-8"),
        headers={
            "Content-Type": "application/soap+xml; charset=utf-8",
            "SOAPAction": '""',
        },
        method="POST",
    )
    # Basic auth
    import base64

    token = base64.b64encode(f"{USER}:{PASS}".encode()).decode()
    req.add_header("Authorization", f"Basic {token}")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except Exception:
        return None


def try_ffmpeg_grab(url: str, outfile: Path, timeout_sec: int = 12) -> tuple[bool, str]:
    """ffmpeg ile tek kare al."""
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-rtsp_transport",
        "tcp",
        "-timeout",
        "5000000",
        "-i",
        url,
        "-frames:v",
        "1",
        "-y",
        str(outfile),
    ]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
        )
        if proc.returncode == 0 and outfile.exists() and outfile.stat().st_size > 0:
            return True, "ok"
        err = (proc.stderr or proc.stdout or "").strip()
        if not err:
            err = f"ffmpeg exit {proc.returncode}"
        # redact password
        err = err.replace(PASS, "***")
        return False, err[:300]
    except subprocess.TimeoutExpired:
        return False, "timeout"
    except Exception as e:
        return False, str(e)[:300]


def try_opencv_grab(url: str, outfile: Path, timeout_sec: int = 10) -> tuple[bool, str]:
    import cv2

    os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = (
        "rtsp_transport;tcp|allowed_media_types;video"
    )
    cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
    try:
        cap.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, timeout_sec * 1000)
        cap.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC, timeout_sec * 1000)
    except Exception:
        pass
    if not cap.isOpened():
        cap.release()
        return False, "opencv open failed"
    ok, frame = cap.read()
    cap.release()
    if not ok or frame is None:
        return False, "opencv read failed"
    cv2.imwrite(str(outfile), frame)
    if outfile.exists() and outfile.stat().st_size > 0:
        return True, "ok"
    return False, "write failed"


def redact(url: str) -> str:
    return url.replace(PASS, "***")


def probe_camera(cam: dict) -> dict:
    ip = cam["ip"]
    kind = cam["kind"]
    result = {
        "id": cam["id"],
        "name": cam["name"],
        "ip": ip,
        "kind": kind,
        "status": "fail",
        "url": None,
        "snapshot": None,
        "error": None,
        "tried": [],
    }

    urls: list[str] = []
    if kind == "dahua":
        urls.extend(dahua_urls(ip))
    else:
        onvif_uri = onvif_get_stream_uri(ip)
        if onvif_uri:
            urls.append(onvif_uri)
            result["tried"].append({"url": redact(onvif_uri), "source": "onvif"})
        urls.extend(oem_urls(ip))

    # unique preserve order
    seen = set()
    unique_urls = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            unique_urls.append(u)

    outfile = OUT_DIR / f"{cam['id']}_{cam['name'].replace(' ', '_')}.jpg"

    for url in unique_urls:
        # remove leftover file
        if outfile.exists():
            outfile.unlink()

        ok, msg = try_ffmpeg_grab(url, outfile)
        attempt = {"url": redact(url), "method": "ffmpeg", "ok": ok, "msg": msg}
        result["tried"].append(attempt)
        print(f"  [{cam['id']}] ffmpeg {redact(url)} -> {msg}", flush=True)
        if ok:
            result["status"] = "ok"
            result["url"] = redact(url)
            result["snapshot"] = str(outfile)
            return result

        # auth errors: still try opencv once, then continue paths
        if "401" in msg or "Unauthorized" in msg.lower() or "authorization" in msg.lower():
            result["error"] = "auth_failed"
            # keep trying other paths in case path matters more than auth message

        ok2, msg2 = try_opencv_grab(url, outfile)
        attempt2 = {"url": redact(url), "method": "opencv", "ok": ok2, "msg": msg2}
        result["tried"].append(attempt2)
        print(f"  [{cam['id']}] opencv {redact(url)} -> {msg2}", flush=True)
        if ok2:
            result["status"] = "ok"
            result["url"] = redact(url)
            result["snapshot"] = str(outfile)
            return result

    if not result["error"]:
        result["error"] = "no_working_url"
    return result


def main() -> int:
    print("=== 4 kamera baglanti testi ===", flush=True)
    results = []
    for cam in CAMERAS:
        print(f"\n-- {cam['name']} ({cam['ip']}) [{cam['kind']}] --", flush=True)
        r = probe_camera(cam)
        results.append(r)

    report_path = Path(__file__).resolve().parent / "report.json"
    report_path.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")

    print("\n=== SONUC ===")
    print(f"{'IP':<16} {'Ad':<22} {'Durum':<8} {'URL / Hata'}")
    print("-" * 90)
    for r in results:
        detail = r["url"] or r["error"] or "?"
        print(f"{r['ip']:<16} {r['name']:<22} {r['status']:<8} {detail}")

    print(f"\nRapor: {report_path}")
    print(f"Kareler: {OUT_DIR}")
    ok_count = sum(1 for r in results if r["status"] == "ok")
    return 0 if ok_count == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
