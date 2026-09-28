#!/usr/bin/env python3
"""4 kamera izleme — tarayici eklentisi yok, RTSP ile hafif pencere."""

from __future__ import annotations

import os
import time

import cv2
import numpy as np

USER, PASS = "admin", "admin"

CAMS = [
    {
        "id": "152",
        "name": "Giris on",
        "url": f"rtsp://{USER}:{PASS}@172.16.21.152:554/cam/realmonitor?channel=1&subtype=1",
        "hold": True,
    },
    {
        "id": "153",
        "name": "Giris arka",
        "url": f"rtsp://{USER}:{PASS}@172.16.21.153:554/cam/realmonitor?channel=1&subtype=1",
        "hold": True,
    },
    {
        "id": "164",
        "name": "Cikis on",
        "url": f"rtsp://{USER}:{PASS}@172.16.21.164:554/media/video1",
        "hold": False,
    },
    {
        "id": "165",
        "name": "Cikis arka",
        "url": f"rtsp://{USER}:{PASS}@172.16.21.165:554/media/video1",
        "hold": False,
    },
]

CELL_W, CELL_H = 640, 360


class _QuietStderr:
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


def open_cam(url: str, low_latency: bool = True) -> cv2.VideoCapture:
    os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = (
        "rtsp_transport;tcp|allowed_media_types;video|fflags;discardcorrupt"
    )
    with _QuietStderr():
        cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
    try:
        if low_latency:
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        cap.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 8000)
        cap.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC, 8000)
    except Exception:
        pass
    return cap


def read_frame(cap: cv2.VideoCapture, tries: int = 8, need: int = 1):
    frame = None
    got = 0
    with _QuietStderr():
        for _ in range(tries):
            ok, f = cap.read()
            if ok and f is not None and f.size > 0:
                frame = f
                got += 1
                if got >= need:
                    break
    return frame


def grab_brief(url: str):
    cap = open_cam(url, low_latency=False)
    frame = read_frame(cap, tries=20, need=2) if cap.isOpened() else None
    try:
        cap.release()
    except Exception:
        pass
    return frame


def tile(frame: np.ndarray | None, title: str) -> np.ndarray:
    cell = np.zeros((CELL_H, CELL_W, 3), dtype=np.uint8)
    if frame is None:
        cv2.putText(cell, "baglanti yok", (40, CELL_H // 2), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 0, 255), 2)
    else:
        cell = cv2.resize(frame, (CELL_W, CELL_H), interpolation=cv2.INTER_AREA)
    cv2.rectangle(cell, (0, 0), (CELL_W - 1, 36), (0, 0, 0), -1)
    cv2.putText(cell, title, (10, 26), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
    return cell


def main() -> int:
    try:
        cv2.setLogLevel(0)
    except Exception:
        pass

    print("4 kamera izleme. Cikis: Q veya Esc", flush=True)
    print("Dahua alt yayin (hafif). Neutron kisa kare — web eklentisi gerekmez.", flush=True)

    caps: list = []
    last: list[np.ndarray | None] = [None] * 4
    last_snap = [0.0] * 4

    for cam in CAMS:
        if cam["hold"]:
            cap = open_cam(cam["url"])
            print(f"  {cam['id']} {cam['name']}: {'OK' if cap.isOpened() else 'HATA'}", flush=True)
            caps.append(cap if cap.isOpened() else None)
        else:
            print(f"  {cam['id']} {cam['name']}: kisa oturum", flush=True)
            caps.append(None)

    win = "Kameralar  (Q = kapat)"
    cv2.namedWindow(win, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(win, CELL_W * 2, CELL_H * 2)

    try:
        while True:
            now = time.time()
            for i, cam in enumerate(CAMS):
                if cam["hold"]:
                    cap = caps[i]
                    if cap is None:
                        if int(now) % 8 == i:
                            nc = open_cam(cam["url"])
                            if nc.isOpened():
                                caps[i] = nc
                        continue
                    f = read_frame(cap, tries=3, need=1)
                    if f is None:
                        try:
                            cap.release()
                        except Exception:
                            pass
                        caps[i] = None
                    else:
                        last[i] = f
                # Neutron asagida tek tek

            due = [
                i
                for i, cam in enumerate(CAMS)
                if not cam["hold"] and now - last_snap[i] >= 4.0
            ]
            if due:
                i = min(due, key=lambda j: last_snap[j])
                last[i] = grab_brief(CAMS[i]["url"])
                last_snap[i] = now

            mosaic = np.vstack(
                [
                    np.hstack(
                        [
                            tile(last[0], "152 Giris on"),
                            tile(last[1], "153 Giris arka"),
                        ]
                    ),
                    np.hstack(
                        [
                            tile(last[2], "164 Cikis on"),
                            tile(last[3], "165 Cikis arka"),
                        ]
                    ),
                ]
            )
            cv2.imshow(win, mosaic)
            key = cv2.waitKey(30) & 0xFF
            if key in (ord("q"), ord("Q"), 27):
                break
    except KeyboardInterrupt:
        pass
    finally:
        for c in caps:
            if c is not None:
                c.release()
        cv2.destroyAllWindows()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
