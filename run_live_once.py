#!/usr/bin/env python3
import json
import time
from datetime import datetime
from pathlib import Path

import cv2
import easyocr

from live_plate_ocr import (
    CAMERAS,
    OUT_DIR,
    activity_score,
    grab,
    open_cam,
    read_plates,
)


def main() -> None:
    reader = easyocr.Reader(["en", "tr"], gpu=False, verbose=False)
    snap = Path(__file__).resolve().parent / "snapshots" / "153_Giris_kantar_arka.jpg"
    validated = read_plates(reader, cv2.imread(str(snap)))
    print("validation:", validated, flush=True)

    caps = []
    for cid, name, url in CAMERAS:
        cap = open_cam(url)
        caps.append(cap if cap.isOpened() else None)

    live = []
    start = time.time()
    i = 0
    while time.time() - start < 40:
        cid, name, _ = CAMERAS[i % 4]
        cap = caps[i % 4]
        i += 1
        if not cap:
            continue
        frame = grab(cap)
        if frame is None:
            continue
        sc = activity_score(frame)
        ts = datetime.now().strftime("%H:%M:%S")
        if sc < 8:
            print(f"[{ts}] {cid} bos {sc:.1f}", flush=True)
            continue
        plates = read_plates(reader, frame)
        print(f"[{ts}] {cid} act={sc:.1f} plates={plates}", flush=True)
        if plates:
            live.append({"cam": cid, "time": ts, "plates": plates})
            p = plates[0][0]
            ann = frame.copy()
            cv2.putText(ann, p, (40, 70), cv2.FONT_HERSHEY_SIMPLEX, 1.8, (0, 255, 0), 3)
            out = OUT_DIR / f"live_{cid}_{p.replace(' ', '_')}.jpg"
            cv2.imwrite(str(out), ann)

    for c in caps:
        if c:
            c.release()

    report = {
        "validation_snapshot_153": [
            {"text": p, "conf": c, "raw": r} for p, c, r in validated
        ],
        "live_window_sec": 40,
        "live_detections": [
            {
                "cam": x["cam"],
                "time": x["time"],
                "plates": [
                    {"text": p, "conf": c, "raw": r} for p, c, r in x["plates"]
                ],
            }
            for x in live
        ],
        "note": "Canli pencerede arac yoksa tespit bos; dogrulama eski kamyon karesinden.",
    }
    path = OUT_DIR / "live_report.json"
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print("wrote", path, flush=True)


if __name__ == "__main__":
    main()
