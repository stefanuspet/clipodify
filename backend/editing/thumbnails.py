"""Kandidat thumbnail dari ORANG UTAMA klip (identitas wajah lintas shot).

Frame diambil hanya dari shot tempat orang itu muncul (close-up diutamakan), lalu dinilai:
tajam, mata terbuka, ekspresi mulut, menghadap kamera, ukuran wajah, emosi audio.
Output: potret 1080×1920 berpusat di wajahnya (siap jadi foto dasar thumbnail).
"""

from __future__ import annotations

import glob
import json
import logging
import os

import cv2
import numpy as np

logger = logging.getLogger(__name__)

_STEP_S = 0.4
_MAX_SAMPLES = 90
_MIN_GAP_S = 1.2
_W = {"sharp": 0.25, "eyes": 0.25, "mouth": 0.15, "frontal": 0.15, "size": 0.10, "emotion": 0.10}


def _clamp01(v: float) -> float:
    return max(0.0, min(1.0, float(v)))


def person_names(candidate_id: int) -> dict[str, str]:
    """Nama tiap ID dari rencana edit terakhir (kalau Auto Edit pernah dijalankan)."""
    from backend.editing.context import cache_dir
    files = sorted(glob.glob(os.path.join(cache_dir(candidate_id), "plan_*.json")), key=os.path.getmtime)
    if not files:
        return {}
    with open(files[-1], encoding="utf-8") as f:
        plan = json.load(f).get("plan") or {}
    return {p["id"]: p["name"] for p in plan.get("people") or [] if p.get("confidence", 0) >= 0.5}


def _samples(ctx: dict, person_id: str) -> list[tuple[float, int, str]]:
    entries = [(k, pid) for k, sh in enumerate(ctx["shots"])
               for pid, iid in (sh.get("identity") or {}).items() if iid == person_id]
    # Close-up (1 orang) dulu: wajah besar, tidak terpotong cell
    entries.sort(key=lambda e: ctx["shots"][e[0]]["layout"] != "single")
    out = []
    for k, pid in entries:
        sh = ctx["shots"][k]
        t = sh["t_start"] + 0.2
        while t < sh["t_end"] - 0.2:
            out.append((round(t, 2), k, pid))
            t += _STEP_S
    if len(out) > _MAX_SAMPLES:
        idx = np.linspace(0, len(out) - 1, _MAX_SAMPLES).astype(int)
        out = [out[i] for i in idx]
    return out


def _emotion_at(ctx: dict, t: float) -> float:
    for e in ctx.get("audio_emotions") or []:
        if e["s"] <= t < e["e"] and e["emotion"] in ("laughing", "excited"):
            return float(e["intensity"])
    return 0.0


def person_thumbnails(ctx: dict, out_dir: str, n: int = 12, person_id: str | None = None,
                      names: dict[str, str] | None = None) -> list[dict]:
    import mediapipe as mp
    from backend.analyzer.face_detector import FaceBox, detect_faces
    from backend.analyzer.grid_composer import _crop_rect, _make_mouth_landmarker
    from backend.analyzer.person_tracker import BBox

    people = ctx.get("people") or []
    if not people:
        return []
    main = next((p for p in people if p["id"] == person_id), people[0])
    label = (names or {}).get(main["id"], main["id"])
    lm = _make_mouth_landmarker()
    vid_w, vid_h = ctx["width"], ctx["height"]
    os.makedirs(out_dir, exist_ok=True)

    cap = cv2.VideoCapture(ctx["clip_path"])
    scored = []
    for t, k, pid in _samples(ctx, main["id"]):
        sh = ctx["shots"][k]
        fb0 = FaceBox(**sh["face_bboxes"][pid]) if pid in sh["face_bboxes"] else None
        if fb0 is None:
            continue
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(t * ctx["fps"]))
        ok, frame = cap.read()
        if not ok:
            continue

        # Wajah saat ini (orang bergerak di dalam shot)
        side = int(max(fb0.w, fb0.h) * 2.2)
        x1, y1 = max(0, fb0.cx - side // 2), max(0, fb0.cy - side // 2)
        crop = frame[y1:y1 + side, x1:x1 + side]
        faces = detect_faces(crop) if crop.size else []
        if not faces:
            continue
        f = min(faces, key=lambda f: abs((f.bbox[0] + f.bbox[2]) / 2 - crop.shape[1] / 2)
                + abs((f.bbox[1] + f.bbox[3]) / 2 - crop.shape[0] / 2))
        bx1, by1, bx2, by2 = [int(v) for v in f.bbox]
        fb = FaceBox(x=x1 + bx1, y=y1 + by1, w=max(1, bx2 - bx1), h=max(1, by2 - by1),
                     conf=float(f.det_score))

        face_img = frame[max(0, fb.y):fb.y + fb.h, max(0, fb.x):fb.x + fb.w]
        if face_img.size == 0:
            continue
        gray = cv2.cvtColor(cv2.resize(face_img, (160, 160)), cv2.COLOR_BGR2GRAY)
        sharp = _clamp01(cv2.Laplacian(gray, cv2.CV_64F).var() / 250.0)

        kps = f.kps
        eye_d = max(1.0, float(np.linalg.norm(kps[1] - kps[0])))
        frontal = 1 - _clamp01(abs(kps[2][0] - (kps[0][0] + kps[1][0]) / 2) / (eye_d / 2))

        eyes = mouth = 0.5
        if lm is not None:
            s2 = int(max(fb.w, fb.h) * 1.8)
            sx, sy = max(0, fb.cx - s2 // 2), max(0, fb.cy - s2 // 2)
            sq = frame[sy:sy + s2, sx:sx + s2]
            if sq.size:
                res = lm.detect(mp.Image(image_format=mp.ImageFormat.SRGB,
                                         data=cv2.cvtColor(cv2.resize(sq, (256, 256)), cv2.COLOR_BGR2RGB)))
                if res.face_landmarks:
                    p = res.face_landmarks[0]
                    ear = np.mean([abs(p[159].y - p[145].y) / max(1e-3, abs(p[33].x - p[133].x)),
                                   abs(p[386].y - p[374].y) / max(1e-3, abs(p[362].x - p[263].x))])
                    ys = [q.y for q in p]
                    mar = abs(p[13].y - p[14].y) / max(1e-3, max(ys) - min(ys))
                    eyes = _clamp01((ear - 0.12) / 0.14)
                    mouth = _clamp01(mar / 0.10)

        size = _clamp01(fb.h / (vid_h * 0.30))
        emo = _clamp01(_emotion_at(ctx, t))
        parts = {"sharp": sharp, "eyes": eyes, "mouth": mouth, "frontal": frontal, "size": size, "emotion": emo}
        score = sum(_W[k2] * v for k2, v in parts.items())
        scored.append((score, t, k, pid, fb, parts))
    cap.release()
    if lm is not None:
        lm.close()

    scored.sort(key=lambda x: -x[0])
    picked = []
    for item in scored:
        if all(abs(item[1] - p[1]) >= _MIN_GAP_S for p in picked):
            picked.append(item)
        if len(picked) >= n:
            break

    cap = cv2.VideoCapture(ctx["clip_path"])
    out = []
    for score, t, k, pid, fb, parts in sorted(picked, key=lambda x: -x[0]):
        sh = ctx["shots"][k]
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(t * ctx["fps"]))
        ok, frame = cap.read()
        if not ok:
            continue
        bb = BBox(**sh["bboxes"][pid])
        lo, hi = sh["x_bounds"].get(pid, (0, vid_w)) if len(sh["persons"]) > 1 else (0, vid_w)
        w, h, rx, ry, _ = _crop_rect(bb, vid_w, vid_h, 1080, 1920, fb, lo, hi)
        portrait = cv2.resize(frame[ry:ry + h, rx:rx + w], (1080, 1920), interpolation=cv2.INTER_LANCZOS4)
        name = f"thumb_{main['id']}_{t:06.2f}.jpg"
        cv2.imwrite(os.path.join(out_dir, name), portrait, [cv2.IMWRITE_JPEG_QUALITY, 92])
        out.append({
            "time_s": t, "filename": name, "face_count": 1, "face_confidence": round(fb.conf, 3),
            "emotion": "laughing/excited" if parts["emotion"] > 0 else "neutral", "energy": parts["emotion"],
            "score": round(score, 4), "person": label, "person_id": main["id"],
            "reasons": {k2: round(v, 2) for k2, v in parts.items()},
        })
    cap.release()
    return out
