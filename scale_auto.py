#!/usr/bin/env python3
"""Kantar otomasyon state machine — runtime cycle, VisitStore'a sadece capture'da yazar."""

from __future__ import annotations

import logging
import statistics
import threading
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

from irsaliye import format_plate, plate_extends, plate_key
from kantar_ifs import SCALES, _cfg

log = logging.getLogger("kantar")


class ScaleState:
    SCALE_EMPTY = "SCALE_EMPTY"
    PLATE_CANDIDATE = "PLATE_CANDIDATE"
    VEHICLE_ENTERING = "VEHICLE_ENTERING"
    WEIGHT_RISING = "WEIGHT_RISING"
    WEIGHT_STABILIZING = "WEIGHT_STABILIZING"
    WEIGHT_STABLE = "WEIGHT_STABLE"
    WEIGHT_CAPTURED = "WEIGHT_CAPTURED"
    VEHICLE_LEAVING = "VEHICLE_LEAVING"
    WAITING_SCALE_CLEAR = "WAITING_SCALE_CLEAR"
    MANUAL_REVIEW = "MANUAL_REVIEW"
    COMM_ERROR = "COMM_ERROR"
    RECOVERY = "RECOVERY"


# Mevcut kantar_ifs kamera anahtarları: 153 giriş, 165 çıkış.
DIRECTION = {"153": "ENTRY", "165": "EXIT"}
if set(DIRECTION) != set(SCALES):
    raise RuntimeError("scale DIRECTION kameraları kantar_ifs.SCALES ile uyuşmuyor")


def _settings() -> dict:
    cfg = _cfg()
    return {
        "empty": float(cfg["empty_kg"]),
        "clear_sec": float(cfg["scale_clear_duration_seconds"]),
        "stable_n": max(2, int(cfg["stable_sample_count"])),
        "stable_tol": float(cfg["stable_delta_kg"]),
        "stable_sec": float(cfg["stable_duration_seconds"]),
        "ocr_window": float(cfg["ocr_candidate_window_seconds"]),
        "ocr_timeout": float(cfg["ocr_candidate_timeout_seconds"]),
        "ocr_min": max(1, int(cfg["ocr_min_confirmations"])),
        "ocr_min_conf": float(cfg["ocr_min_confidence"]),
        "match_window": float(cfg["plate_scale_match_window_seconds"]),
        "stab_timeout": float(cfg["stabilization_timeout_seconds"]),
        "enter_timeout": float(cfg["vehicle_entering_timeout_seconds"]),
        "clear_timeout": float(cfg["waiting_scale_clear_timeout_seconds"]),
        "debug": bool(cfg.get("scale_debug")),
    }


def _lev(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            ins = cur[j - 1] + 1
            delete = prev[j] + 1
            sub = prev[j - 1] + (ca != cb)
            cur.append(min(ins, delete, sub))
        prev = cur
    return prev[-1]


def _conf(value: Any) -> float | None:
    if value is None:
        return None
    try:
        c = float(value)
    except (TypeError, ValueError):
        return None
    if c > 1.0:
        c = c / 100.0 if c <= 100.0 else 1.0
    if c < 0:
        return None
    return min(c, 1.0)


@dataclass
class PlateHit:
    plate: str
    plate_key: str
    raw: str
    camera: str
    ts: float
    confidence: float | None = None
    image_path: str = ""


@dataclass
class ScaleAction:
    kind: str
    camera: str
    cycle_id: str
    kg: float | None = None
    plate: str | None = None
    raw_plate: str = ""
    reason: str | None = None
    candidates: list[dict] = field(default_factory=list)
    timeline: list[dict] = field(default_factory=list)
    sound: str | None = None


@dataclass
class ScaleSnapshot:
    camera: str
    direction: str
    state: str
    cycle_id: str
    kg: float | None
    samples: list[float]
    candidates: list[dict]
    selected: dict | None
    captured: bool
    reason: str | None
    debug: bool
    timeline: list[str]


class ScaleLane:
    """Tek kantara ait runtime cycle. Disk yazmaz."""

    def __init__(self, camera: str) -> None:
        self.camera = camera
        self.direction = DIRECTION.get(camera, "ENTRY")
        self._lock = threading.RLock()
        self.cycle_id = self._new_cycle()
        self.state = ScaleState.SCALE_EMPTY
        self.state_since = 0.0
        self.hits: list[PlateHit] = []
        self.samples: list[tuple[float, float]] = []
        self.last_kg: float | None = None
        self.last_ok_kg: float | None = None
        self.captured = False
        self.captured_kg: float | None = None
        self.stable_kg: float | None = None
        self.captured_plate: str | None = None
        self.rise_ts: float | None = None
        self.empty_since: float | None = None
        self.stable_since: float | None = None
        self.plate_wait_since: float | None = None
        self.review_reason: str | None = None
        self.comm_prev: str | None = None
        self.started = False
        self.timeline: list[dict] = []
        self._last_sound: str | None = None
        self.open_visit_keys: Callable[[], set[str]] = lambda: set()
        self.has_irsaliye: Callable[[str], bool] = lambda _p: False

    def _new_cycle(self) -> str:
        return f"W-{self.camera}-{uuid.uuid4().hex[:8]}"

    def _cfg(self) -> dict:
        return _settings()

    def _note(self, now: float, action: str, **detail: Any) -> None:
        rec = {"ts": now, "action": action, "detail": {k: v for k, v in detail.items() if v is not None}}
        self.timeline.append(rec)
        self.timeline = self.timeline[-40:]
        log.info(
            "operation=scale_state camera=%s cycle=%s state=%s action=%s kg=%s plate=%s",
            self.camera,
            self.cycle_id,
            self.state,
            action,
            detail.get("kg", self.last_ok_kg),
            detail.get("plate") or self.captured_plate,
        )

    def _go(self, state: str, now: float, *, sound: str | None = None, **detail: Any) -> str | None:
        if state == self.state:
            return None
        prev = self.state
        self.state = state
        self.state_since = now
        self._note(now, state, from_state=prev, **detail)
        play = sound
        if play and play == self._last_sound and state not in {ScaleState.MANUAL_REVIEW, ScaleState.COMM_ERROR}:
            play = None
        if play:
            self._last_sound = play
        return play

    def _is_empty(self, kg: float) -> bool:
        return kg <= self._cfg()["empty"]

    def _in_tolerance(self) -> bool:
        cfg = self._cfg()
        vals = [kg for _ts, kg in self.samples[-cfg["stable_n"] :]]
        if len(vals) < cfg["stable_n"]:
            return False
        return max(vals) - min(vals) <= cfg["stable_tol"]

    def _stable(self, now: float) -> bool:
        cfg = self._cfg()
        if not self._in_tolerance():
            self.stable_since = None
            return False
        if self.stable_since is None:
            self.stable_since = now
        return (now - self.stable_since) >= cfg["stable_sec"]

    def _median(self) -> float | None:
        vals = [kg for _ts, kg in self.samples[-max(3, self._cfg()["stable_n"]) :]]
        if not vals:
            return self.last_ok_kg
        return float(statistics.median(vals))

    def want_ocr(self) -> bool:
        if self.captured:
            return False
        return self.state not in {
            ScaleState.WAITING_SCALE_CLEAR,
            ScaleState.WEIGHT_CAPTURED,
        }

    def needs_poll(self) -> bool:
        return self.state != ScaleState.SCALE_EMPTY or bool(self.hits)

    def snapshot(self) -> ScaleSnapshot:
        scored = self._score_candidates(now=self.samples[-1][0] if self.samples else 0.0)
        selected = scored[0] if scored else None
        return ScaleSnapshot(
            camera=self.camera,
            direction=self.direction,
            state=self.state,
            cycle_id=self.cycle_id,
            kg=self.last_kg if self.state != ScaleState.COMM_ERROR else self.last_ok_kg,
            samples=[kg for _ts, kg in self.samples[-8:]],
            candidates=scored,
            selected=selected if selected and selected.get("confirmed") else selected,
            captured=self.captured,
            reason=self.review_reason,
            debug=self._cfg()["debug"],
            timeline=[f"{e.get('action')}" for e in self.timeline[-12:]],
        )

    def on_plate(
        self,
        *,
        plate: str,
        raw: str,
        confidence: Any,
        image_path: str,
        now: float,
    ) -> list[ScaleAction]:
        with self._lock:
            formatted = format_plate(plate) or plate
            key = plate_key(formatted)
            if not key:
                return []
            hit = PlateHit(
                plate=formatted,
                plate_key=key,
                raw=(raw or plate or "").strip(),
                camera=self.camera,
                ts=now,
                confidence=_conf(confidence),
                image_path=image_path or "",
            )
            self.hits.append(hit)
            cutoff = now - max(self._cfg()["ocr_timeout"], self._cfg()["ocr_window"]) - 5
            self.hits = [h for h in self.hits if h.ts >= cutoff]
            self._note(now, "PLATE_CANDIDATE", plate=formatted, confidence=hit.confidence)
            actions: list[ScaleAction] = []
            sound = None
            if self.state == ScaleState.SCALE_EMPTY:
                sound = self._go(ScaleState.PLATE_CANDIDATE, now, plate=formatted)
            elif self.state == ScaleState.WEIGHT_STABLE and not self.captured:
                actions.extend(self._try_capture(now))
            snap = self.snapshot()
            actions.append(self._snap_action("snapshot", sound=sound))
            _ = snap
            return actions

    def on_weight(self, snap: dict | None, now: float) -> list[ScaleAction]:
        with self._lock:
            return self._on_weight_locked(snap, now)

    def tick(self, now: float) -> list[ScaleAction]:
        with self._lock:
            extra: list[ScaleAction] = []
            if self.state == ScaleState.WEIGHT_STABLE and not self.captured:
                extra.extend(self._try_capture(now))
            return extra + self._timeouts(now)

    def _on_weight_locked(self, snap: dict | None, now: float) -> list[ScaleAction]:
        actions: list[ScaleAction] = []
        if snap is None:
            if self.state != ScaleState.COMM_ERROR:
                self.comm_prev = self.state
                sound = self._go(ScaleState.COMM_ERROR, now, sound="comm")
                actions.append(self._snap_action("comm_error", sound=sound))
            self.last_kg = None
            return actions + self._timeouts(now)

        kg = float(snap.get("kg") or 0)
        self.last_kg = kg
        if not self._is_empty(kg):
            self.last_ok_kg = kg
        self.samples.append((now, kg))
        self.samples = self.samples[-20:]

        if self.state == ScaleState.COMM_ERROR:
            restore = self.comm_prev or ScaleState.SCALE_EMPTY
            self.comm_prev = None
            self._go(restore, now, kg=kg)
        if not self.started:
            self.started = True
            if not self._is_empty(kg):
                self.review_reason = "RECOVERY_OCCUPIED"
                sound = self._go(ScaleState.RECOVERY, now, kg=kg, sound="review")
                actions.append(
                    self._persist(
                        "review",
                        kg=kg,
                        reason="RECOVERY_OCCUPIED",
                        sound=sound,
                    )
                )
                return actions

        if self.state == ScaleState.RECOVERY:
            if self._is_empty(kg):
                if self.empty_since is None:
                    self.empty_since = now
                elif now - self.empty_since >= self._cfg()["clear_sec"]:
                    actions.extend(self._reset_empty(now))
            else:
                self.empty_since = None
            return actions + [self._snap_action("snapshot")]

        if self._is_empty(kg):
            return actions + self._on_empty_sample(now, kg)
        self.empty_since = None
        return actions + self._on_loaded_sample(now, kg)

    def _on_empty_sample(self, now: float, kg: float) -> list[ScaleAction]:
        cfg = self._cfg()
        actions: list[ScaleAction] = []
        if self.state == ScaleState.PLATE_CANDIDATE:
            return actions + self._timeouts(now) + [self._snap_action("snapshot")]
        if self.state in {
            ScaleState.WEIGHT_CAPTURED,
            ScaleState.WAITING_SCALE_CLEAR,
            ScaleState.VEHICLE_LEAVING,
            ScaleState.MANUAL_REVIEW,
        }:
            if self.state == ScaleState.WEIGHT_CAPTURED:
                self._go(ScaleState.WAITING_SCALE_CLEAR, now, kg=kg)
            elif self.state == ScaleState.MANUAL_REVIEW and self.captured:
                self._go(ScaleState.WAITING_SCALE_CLEAR, now, kg=kg)
            elif self.state not in {ScaleState.WAITING_SCALE_CLEAR, ScaleState.VEHICLE_LEAVING}:
                self._go(ScaleState.VEHICLE_LEAVING, now, kg=kg)
            if self.empty_since is None:
                self.empty_since = now
            if now - self.empty_since >= cfg["clear_sec"]:
                actions.extend(self._reset_empty(now))
            return actions + [self._snap_action("snapshot")]
        if self.state in {
            ScaleState.VEHICLE_ENTERING,
            ScaleState.WEIGHT_RISING,
            ScaleState.WEIGHT_STABILIZING,
            ScaleState.WEIGHT_STABLE,
        }:
            if self.empty_since is None:
                self.empty_since = now
            if now - self.empty_since >= cfg["clear_sec"]:
                self._note(now, "WEIGHT_DROPPED_BEFORE_CAPTURE", kg=kg)
                if self.state in {
                    ScaleState.WEIGHT_STABLE,
                    ScaleState.WEIGHT_STABILIZING,
                }:
                    actions.extend(self._abandon_without_plate(now))
                actions.extend(self._reset_empty(now))
            return actions + [self._snap_action("snapshot")]
        if self.state == ScaleState.SCALE_EMPTY:
            self.empty_since = now if self.empty_since is None else self.empty_since
        return actions + self._timeouts(now) + [self._snap_action("snapshot")]

    def _on_loaded_sample(self, now: float, kg: float) -> list[ScaleAction]:
        actions: list[ScaleAction] = []
        if self.state == ScaleState.WAITING_SCALE_CLEAR and self.captured:
            self._go(ScaleState.VEHICLE_LEAVING, now, kg=kg)
            return [self._snap_action("snapshot")]
        if self.captured:
            return [self._snap_action("snapshot")]
        if self.state == ScaleState.MANUAL_REVIEW:
            return [self._snap_action("snapshot")]
        if self.state in {ScaleState.SCALE_EMPTY, ScaleState.PLATE_CANDIDATE}:
            self.rise_ts = now
            self.stable_since = None
            sound = self._go(ScaleState.VEHICLE_ENTERING, now, kg=kg)
            return [self._snap_action("snapshot", sound=sound)]
        if self.state == ScaleState.VEHICLE_ENTERING:
            self._go(ScaleState.WEIGHT_RISING, now, kg=kg)
        if self.state == ScaleState.WEIGHT_RISING:
            if self._in_tolerance():
                self._go(ScaleState.WEIGHT_STABILIZING, now, kg=kg)
            else:
                return actions + self._timeouts(now) + [self._snap_action("snapshot")]
        if self.state == ScaleState.WEIGHT_STABILIZING:
            if not self._in_tolerance():
                self._go(ScaleState.WEIGHT_RISING, now, kg=kg)
                return [self._snap_action("snapshot")]
            if not self._stable(now):
                return actions + self._timeouts(now) + [self._snap_action("snapshot")]
            seated = self._median()
            self.stable_kg = seated
            self._go(ScaleState.WEIGHT_STABLE, now, kg=seated)
        if self.state == ScaleState.WEIGHT_STABLE:
            actions.extend(self._try_capture(now))
        return actions + self._timeouts(now) + [self._snap_action("snapshot")]

    def _try_capture(self, now: float) -> list[ScaleAction]:
        if self.captured:
            return []
        kg = self._median()
        decision = self._select_plate(now)
        if decision.get("status") == "confirmed":
            plate = str(decision["plate"])
            self.captured = True
            self.captured_kg = kg
            self.captured_plate = plate
            self._note(now, "PLATE_CONFIRMED", plate=plate, **{k: decision[k] for k in ("score", "why") if k in decision})
            self._note(now, "WEIGHT_CAPTURED", kg=kg, plate=plate)
            sound = self._go(ScaleState.WEIGHT_CAPTURED, now, kg=kg, plate=plate, sound="capture")
            self._go(ScaleState.WAITING_SCALE_CLEAR, now, kg=kg)
            return [
                self._persist(
                    "capture",
                    kg=kg,
                    plate=plate,
                    raw_plate=str(decision.get("raw") or plate),
                    sound=sound,
                    candidates=decision.get("all") or [],
                )
            ]
        if decision.get("status") == "ambiguous":
            self.captured = True
            self.captured_kg = kg
            self.review_reason = "AMBIGUOUS_PLATE"
            sound = self._go(ScaleState.MANUAL_REVIEW, now, kg=kg, sound="review")
            return [
                self._persist(
                    "review",
                    kg=kg,
                    reason="AMBIGUOUS_PLATE",
                    sound=sound,
                    candidates=decision.get("all") or [],
                )
            ]
        if self.plate_wait_since is None:
            self.plate_wait_since = now
        if now - self.plate_wait_since < self._cfg()["ocr_timeout"]:
            return []
        return self._finish_missing_plate(now, kg, decision)

    def _abandon_without_plate(self, now: float) -> list[ScaleAction]:
        """Oturmuş tartım varken araç çıktı — plaka yoksa TANIMSIZ kayıt."""
        if self.captured:
            return []
        kg = self.captured_kg
        if kg is None:
            kg = self.stable_kg
        if kg is None:
            kg = self.last_ok_kg
        if kg is None or self._is_empty(float(kg)):
            return []
        decision = self._select_plate(now)
        return self._finish_missing_plate(now, float(kg), decision)

    def _finish_missing_plate(
        self, now: float, kg: float, decision: dict
    ) -> list[ScaleAction]:
        if decision.get("plate"):
            plate = str(decision["plate"])
            self.captured = True
            self.captured_kg = kg
            self.captured_plate = plate
            self._note(now, "PLATE_CONFIRMED", plate=plate, why="waited")
            self._note(now, "WEIGHT_CAPTURED", kg=kg, plate=plate)
            sound = self._go(ScaleState.WEIGHT_CAPTURED, now, kg=kg, plate=plate, sound="capture")
            self._go(ScaleState.WAITING_SCALE_CLEAR, now, kg=kg)
            return [
                self._persist(
                    "capture",
                    kg=kg,
                    plate=plate,
                    raw_plate=str(decision.get("raw") or plate),
                    sound=sound,
                    candidates=decision.get("all") or [],
                )
            ]
        self.captured = True
        self.captured_kg = kg
        self.review_reason = "PLATE_NOT_DETECTED"
        sound = self._go(ScaleState.MANUAL_REVIEW, now, kg=kg, sound="review")
        return [
            self._persist(
                "review",
                kg=kg,
                reason="PLATE_NOT_DETECTED",
                sound=sound,
                candidates=decision.get("all") or [],
            )
        ]

    def _score_candidates(self, now: float) -> list[dict]:
        cfg = self._cfg()
        rise = self.rise_ts or now
        keep = max(cfg["ocr_window"], cfg["ocr_timeout"], cfg["match_window"], 180.0)
        window_start = now - keep
        if self.rise_ts:
            window_start = min(window_start, self.rise_ts - keep)
        grouped: dict[str, dict] = {}
        for hit in self.hits:
            if hit.camera != self.camera:
                continue
            if hit.ts < window_start:
                continue
            rec = grouped.setdefault(
                hit.plate_key,
                {
                    "plate": hit.plate,
                    "raw": hit.raw,
                    "key": hit.plate_key,
                    "count": 0,
                    "confs": [],
                    "camera": hit.camera,
                    "last_ts": hit.ts,
                    "best_dt": abs(hit.ts - rise),
                },
            )
            rec["count"] += 1
            rec["last_ts"] = max(rec["last_ts"], hit.ts)
            rec["best_dt"] = min(rec["best_dt"], abs(hit.ts - rise))
            rec["plate"] = hit.plate
            if hit.confidence is not None:
                rec["confs"].append(hit.confidence)
        for short in list(grouped):
            longs = [k for k in grouped if k != short and plate_extends(short, k)]
            if len(longs) != 1:
                continue
            dest, src = grouped[longs[0]], grouped.pop(short)
            dest["count"] += src["count"]
            dest["confs"].extend(src["confs"])
            dest["last_ts"] = max(dest["last_ts"], src["last_ts"])
            dest["best_dt"] = min(dest["best_dt"], src["best_dt"])
        open_keys = set()
        try:
            open_keys = self.open_visit_keys() or set()
        except Exception as exc:
            log.warning("operation=open_visit_keys camera=%s error=%s", self.camera, exc)
        rows: list[dict] = []
        for rec in grouped.values():
            extends = [ok for ok in open_keys if plate_extends(rec["key"], ok)]
            if len(extends) == 1:
                rec["key"] = extends[0]
                rec["plate"] = format_plate(extends[0])
                rec["raw"] = rec["raw"] or rec["plate"]
            confs = rec["confs"]
            avg_conf = (sum(confs) / len(confs)) if confs else None
            if cfg["ocr_min_conf"] > 0 and avg_conf is not None and avg_conf < cfg["ocr_min_conf"]:
                continue
            dt = rec["best_dt"]
            score = rec["count"] * 10.0
            why = [f"ocr={rec['count']}"]
            if avg_conf is not None:
                score += avg_conf * 20.0
                why.append(f"conf={avg_conf:.2f}")
            if dt <= cfg["match_window"]:
                score += max(0.0, (cfg["match_window"] - dt) / cfg["match_window"] * 15.0)
                why.append(f"dt={dt:.1f}s")
            if rec["camera"] == self.camera:
                score += 15.0
                why.append(f"cam={rec['camera']}")
            if self.direction == "EXIT" and rec["key"] in open_keys:
                score += 25.0
                why.append("open_visit")
            elif self.direction == "EXIT" and len(extends) == 1:
                score += 30.0
                why.append("open_extend")
            try:
                if self.has_irsaliye(rec["plate"]):
                    score += 5.0
                    why.append("irsaliye")
            except Exception:
                pass
            confirmed = rec["count"] >= 1
            rec.update(
                {
                    "avg_conf": avg_conf,
                    "score": round(score, 2),
                    "confirmed": confirmed,
                    "why": ",".join(why),
                    "dt": round(dt, 1),
                }
            )
            rows.append(rec)
        rows.sort(key=lambda r: r["score"], reverse=True)
        return rows

    def _select_plate(self, now: float) -> dict:
        rows = self._score_candidates(now)
        for rec in rows:
            log.info(
                "operation=candidate camera=%s plate=%s count=%s conf=%s dt=%s score=%s why=%s",
                self.camera,
                rec.get("plate"),
                rec.get("count"),
                rec.get("avg_conf"),
                rec.get("dt"),
                rec.get("score"),
                rec.get("why"),
            )
        if not rows:
            return {"status": "none", "all": []}
        top = rows[0]
        runner = rows[1] if len(rows) > 1 else None
        if runner and top["key"] != runner["key"]:
            if plate_extends(top["key"], runner["key"]) or plate_extends(runner["key"], top["key"]):
                longer = top if len(top["key"]) >= len(runner["key"]) else runner
                top = longer
            elif (top["score"] - runner["score"]) < 8:
                dist = _lev(top["key"], runner["key"])
                log.info(
                    "operation=candidate_ambiguous camera=%s a=%s b=%s lev=%s",
                    self.camera,
                    top["plate"],
                    runner["plate"],
                    dist,
                )
                return {"status": "ambiguous", "all": rows, "plate": top["plate"]}
        if top.get("confirmed") or top.get("count", 0) >= 1:
            log.info("operation=candidate_selected camera=%s plate=%s %s", self.camera, top["plate"], top["why"])
            return {
                "status": "confirmed",
                "plate": top["plate"],
                "raw": top.get("raw") or top["plate"],
                "score": top.get("score"),
                "why": top.get("why"),
                "all": rows,
            }
        return {"status": "none", "all": rows}

    def _timeouts(self, now: float) -> list[ScaleAction]:
        cfg = self._cfg()
        elapsed = now - self.state_since if self.state_since else 0
        if self.state == ScaleState.PLATE_CANDIDATE and elapsed >= cfg["ocr_timeout"]:
            self._note(now, "PLATE_CANDIDATE_EXPIRED")
            return self._reset_empty(now)
        if self.state == ScaleState.VEHICLE_ENTERING and elapsed >= cfg["enter_timeout"]:
            self._note(now, "TIMEOUT", reason="vehicle_entering")
            if self.last_ok_kg and not self._is_empty(self.last_ok_kg):
                self.review_reason = "STABILIZATION_TIMEOUT"
                sound = self._go(ScaleState.MANUAL_REVIEW, now, sound="review")
                return [
                    self._persist(
                        "review",
                        kg=self.last_ok_kg,
                        reason="STABILIZATION_TIMEOUT",
                        sound=sound,
                    )
                ]
            return self._reset_empty(now)
        if self.state in {ScaleState.WEIGHT_RISING, ScaleState.WEIGHT_STABILIZING} and elapsed >= cfg["stab_timeout"]:
            self._note(now, "TIMEOUT", reason="stabilization")
            self.review_reason = "STABILIZATION_TIMEOUT"
            sound = self._go(ScaleState.MANUAL_REVIEW, now, sound="review")
            return [
                self._persist(
                    "review",
                    kg=self.last_ok_kg,
                    reason="STABILIZATION_TIMEOUT",
                    sound=sound,
                )
            ]
        if self.state == ScaleState.WAITING_SCALE_CLEAR and elapsed >= cfg["clear_timeout"]:
            self._note(now, "TIMEOUT", reason="waiting_scale_clear")
        return []

    def _reset_empty(self, now: float) -> list[ScaleAction]:
        self.cycle_id = self._new_cycle()
        self.hits = []
        self.samples = []
        self.captured = False
        self.captured_kg = None
        self.stable_kg = None
        self.captured_plate = None
        self.rise_ts = None
        self.empty_since = now
        self.stable_since = None
        self.plate_wait_since = None
        self.review_reason = None
        self.timeline = []
        sound = self._go(ScaleState.SCALE_EMPTY, now)
        return [self._snap_action("reset", sound=sound)]

    def _persist(
        self,
        kind: str,
        *,
        kg: float | None,
        reason: str | None = None,
        plate: str | None = None,
        raw_plate: str = "",
        sound: str | None = None,
        candidates: list | None = None,
    ) -> ScaleAction:
        return ScaleAction(
            kind=kind,
            camera=self.camera,
            cycle_id=self.cycle_id,
            kg=kg,
            plate=plate,
            raw_plate=raw_plate,
            reason=reason,
            candidates=list(candidates or []),
            timeline=list(self.timeline),
            sound=sound,
        )

    def _snap_action(self, kind: str, sound: str | None = None) -> ScaleAction:
        snap = self.snapshot()
        return ScaleAction(
            kind=kind,
            camera=self.camera,
            cycle_id=self.cycle_id,
            kg=snap.kg,
            plate=(snap.selected or {}).get("plate") if snap.selected else self.captured_plate,
            reason=snap.reason,
            candidates=snap.candidates,
            sound=sound,
        )


class ScaleYard:
    def __init__(self) -> None:
        self.lanes = {"153": ScaleLane("153"), "165": ScaleLane("165")}

    def lane(self, camera: str) -> ScaleLane:
        if camera not in self.lanes:
            self.lanes[camera] = ScaleLane(camera)
        return self.lanes[camera]
