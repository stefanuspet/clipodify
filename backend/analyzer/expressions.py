"""Analisis ekspresi wajah + jumlah orang per segmen video.

Output dipakai sebagai konteks untuk Gemini agar bisa suggest
ExpressionClip yang tepat saat editing.
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

# Interval sampling antar frame (detik). 0.5 = 2 frame/detik.
_SAMPLE_INTERVAL = 0.5

# Minimum confidence DeepFace untuk dianggap valid
_MIN_CONFIDENCE = 0.70

# Mapping emosi DeepFace → label yang lebih ringkas
_EMOTION_MAP = {
    "angry":    "angry",
    "disgust":  "angry",      # digabung ke angry
    "fear":     "shocked",
    "happy":    "laughing",
    "sad":      "crying",
    "surprise": "shocked",
    "neutral":  "neutral",
}


def analyze_expressions(
    video_path: str,
    sample_interval: float = _SAMPLE_INTERVAL,
    min_segment_duration: float = 0.5,
) -> dict[str, Any]:
    """Analisis video dan kembalikan segmen ekspresi + jumlah wajah.

    Returns:
        {
          "total_frames_sampled": int,
          "duration_s": float,
          "segments": [
            {
              "start": float,
              "end": float,
              "face_count": int,
              "dominant_expression": str,   # "laughing" | "crying" | "angry" | ...
              "confidence": float,
              "raw_emotions": {str: float}, # distribusi lengkap dari DeepFace
            }
          ],
          "summary": {
            "max_people": int,
            "expressions_found": list[str],
            "notable_moments": [{"time": float, "expr": str, "confidence": float}]
          }
        }
    """
    try:
        import cv2
        from deepface import DeepFace
    except ImportError as e:
        raise RuntimeError(
            f"Library tidak tersedia: {e}. Install: pip install opencv-python-headless deepface"
        ) from e

    if not os.path.exists(video_path):
        raise FileNotFoundError(f"Video tidak ditemukan: {video_path}")

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise RuntimeError(f"Tidak bisa buka video: {video_path}")

    fps         = cap.get(cv2.CAP_PROP_FPS) or 24.0
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    duration_s  = total_frames / fps
    step_frames = max(1, int(fps * sample_interval))

    frame_results: list[dict] = []
    frame_idx = 0

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        if frame_idx % step_frames == 0:
            timestamp = frame_idx / fps
            result = _analyze_frame(frame, timestamp, DeepFace)
            if result:
                frame_results.append(result)

        frame_idx += 1

    cap.release()

    segments   = _merge_into_segments(frame_results, min_segment_duration)
    summary    = _build_summary(segments, frame_results)

    return {
        "total_frames_sampled": len(frame_results),
        "duration_s": round(duration_s, 2),
        "segments": segments,
        "summary": summary,
    }


def _analyze_frame(frame, timestamp: float, DeepFace) -> dict | None:
    """Analisis satu frame, kembalikan hasil atau None kalau tidak ada wajah."""
    try:
        results = DeepFace.analyze(
            frame,
            actions=["emotion"],
            enforce_detection=False,
            silent=True,
        )
        # results bisa list (multi-face) atau dict (single)
        if isinstance(results, dict):
            results = [results]

        faces = []
        for r in results:
            conf = r.get("face_confidence", 0)
            if conf < _MIN_CONFIDENCE:
                continue
            dom_raw = r.get("dominant_emotion", "neutral")
            dom     = _EMOTION_MAP.get(dom_raw, "neutral")
            emotions_raw = r.get("emotion", {})
            # Remap ke label kita
            emotions_mapped: dict[str, float] = {}
            for raw_key, score in emotions_raw.items():
                mapped = _EMOTION_MAP.get(raw_key, "neutral")
                emotions_mapped[mapped] = emotions_mapped.get(mapped, 0) + score

            faces.append({
                "dominant": dom,
                "confidence": round(conf, 3),
                "emotions": {k: round(v, 1) for k, v in emotions_mapped.items()},
            })

        if not faces:
            return None

        # Ambil face paling confident sebagai "dominant" frame
        best   = max(faces, key=lambda f: f["confidence"])
        return {
            "timestamp":    round(timestamp, 2),
            "face_count":   len(faces),
            "dominant":     best["dominant"],
            "confidence":   best["confidence"],
            "emotions":     best["emotions"],
        }
    except Exception as exc:
        logger.debug("Frame %.1fs skip: %s", timestamp, exc)
        return None


def _merge_into_segments(
    frames: list[dict],
    min_duration: float,
) -> list[dict]:
    """Gabungkan frame konsekutif dengan ekspresi sama menjadi segmen."""
    if not frames:
        return []

    segments: list[dict] = []
    cur = frames[0].copy()
    cur["start"] = cur["timestamp"]
    cur["end"]   = cur["timestamp"]

    for f in frames[1:]:
        # Lanjutkan segmen jika ekspresi sama dan waktunya berurutan
        if f["dominant"] == cur["dominant"] and f["timestamp"] - cur["end"] <= 1.5:
            cur["end"] = f["timestamp"]
            # Ambil confidence tertinggi di segmen
            if f["confidence"] > cur["confidence"]:
                cur["confidence"] = f["confidence"]
                cur["emotions"]   = f["emotions"]
            cur["face_count"] = max(cur["face_count"], f["face_count"])
        else:
            if cur["end"] - cur["start"] >= min_duration:
                segments.append(_seg_dict(cur))
            cur = f.copy()
            cur["start"] = f["timestamp"]
            cur["end"]   = f["timestamp"]

    if cur["end"] - cur["start"] >= min_duration:
        segments.append(_seg_dict(cur))

    return segments


def _seg_dict(cur: dict) -> dict:
    return {
        "start":               round(cur["start"], 2),
        "end":                 round(cur["end"] + 0.5, 2),  # +0.5 buffer
        "face_count":          cur["face_count"],
        "dominant_expression": cur["dominant"],
        "confidence":          cur["confidence"],
        "raw_emotions":        cur.get("emotions", {}),
    }


def _build_summary(segments: list[dict], frames: list[dict]) -> dict:
    max_people = max((f["face_count"] for f in frames), default=0)
    expressions_found = list({s["dominant_expression"] for s in segments} - {"neutral"})

    # Notable moments: segmen non-neutral dengan confidence > 0.85
    notable = [
        {"time": s["start"], "expr": s["dominant_expression"], "confidence": s["confidence"]}
        for s in segments
        if s["dominant_expression"] != "neutral" and s["confidence"] >= 0.85
    ]
    notable.sort(key=lambda x: x["confidence"], reverse=True)

    return {
        "max_people":        max_people,
        "expressions_found": expressions_found,
        "notable_moments":   notable[:10],  # top 10 moment
    }
