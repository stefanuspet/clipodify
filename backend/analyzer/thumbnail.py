"""Thumbnail candidate extractor.

Strategi pemilihan frame:
  1. Ambil peak_moments dari audio emotion analysis (high energy + non-neutral)
  2. Tambah sample merata dari segmen excited/laughing jika ada
  3. Untuk tiap timestamp kandidat: ekstrak frame via FFmpeg, deteksi wajah
  4. Rank berdasarkan: face_confidence × emotion_weight × energy

Output: list frame JPEG yang siap ditampilkan ke user.
"""

from __future__ import annotations

import logging
import os
import subprocess
import tempfile
from dataclasses import dataclass

logger = logging.getLogger(__name__)

EMOTION_WEIGHT: dict[str, float] = {
    "laughing": 1.0,
    "excited":  0.9,
    "angry":    0.75,
    "shocked":  0.75,
    "sad":      0.4,
    "neutral":  0.3,
    "silence":  0.0,
}

_MAX_CANDIDATES = 12
_MIN_FACE_CONF  = 0.55


@dataclass
class ThumbnailCandidate:
    time_s: float
    jpeg_path: str
    face_count: int
    face_confidence: float
    emotion: str          # dari audio analysis
    energy: float
    score: float


def extract_thumbnail_candidates(
    video_path: str,
    out_dir: str,
    audio_result: dict | None = None,
    n: int = _MAX_CANDIDATES,
) -> list[ThumbnailCandidate]:
    """Ekstrak N frame kandidat thumbnail dari video.

    audio_result: hasil dari analyze_audio() — dipakai untuk prioritas waktu.
    Kalau None, sample merata dari seluruh video.
    """
    duration = _get_duration(video_path)
    if duration <= 0:
        raise RuntimeError("Tidak bisa baca durasi video.")

    timestamps = _pick_timestamps(audio_result, duration, n)
    os.makedirs(out_dir, exist_ok=True)

    candidates: list[ThumbnailCandidate] = []
    for i, (t, emotion, energy) in enumerate(timestamps):
        jpeg_path = os.path.join(out_dir, f"thumb_{i:02d}_{int(t)}s.jpg")
        ok = _extract_frame(video_path, t, jpeg_path)
        if not ok:
            continue

        face_conf, face_count = _detect_face_quality(jpeg_path)
        w = EMOTION_WEIGHT.get(emotion, 0.3)
        score = round(face_conf * 0.5 + w * 0.3 + energy * 0.2, 4)

        candidates.append(ThumbnailCandidate(
            time_s=round(t, 2),
            jpeg_path=jpeg_path,
            face_count=face_count,
            face_confidence=round(face_conf, 3),
            emotion=emotion,
            energy=round(energy, 3),
            score=score,
        ))

    # Urutkan dari skor tertinggi
    candidates.sort(key=lambda c: c.score, reverse=True)
    return candidates[:n]


# ---------------------------------------------------------------- helpers

def _get_duration(video_path: str) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json",
         "-show_format", video_path],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        return 0.0
    import json
    try:
        return float(json.loads(r.stdout)["format"]["duration"])
    except Exception:
        return 0.0


def _pick_timestamps(
    audio_result: dict | None,
    duration: float,
    n: int,
) -> list[tuple[float, str, float]]:
    """Kembalikan list (time_s, emotion, energy) untuk diekstrak."""
    picks: list[tuple[float, str, float]] = []

    if audio_result:
        summary = audio_result.get("summary", {})
        # Peak moments (sudah diurutkan by intensity di analyzer)
        for pm in summary.get("peak_moments", [])[:n // 2]:
            picks.append((pm["time"], pm["emotion"], 0.9))

        # Tambah dari segmen non-neutral
        segs = [s for s in audio_result.get("segments", [])
                if s.get("emotion") not in ("neutral", "silence")]
        # Ambil titik tengah segmen paling energik
        segs.sort(key=lambda s: s.get("avg_energy", 0), reverse=True)
        for s in segs[: n - len(picks)]:
            mid = (s["start"] + s["end"]) / 2
            if not any(abs(mid - p[0]) < 2.0 for p in picks):  # jaga jarak min 2s
                picks.append((mid, s["emotion"], s.get("avg_energy", 0.5)))

    # Fallback: sample merata jika picks masih kurang
    gap = duration / (n - len(picks) + 1)
    t = gap
    while len(picks) < n and t < duration - 1:
        if not any(abs(t - p[0]) < 2.0 for p in picks):
            picks.append((round(t, 2), "neutral", 0.3))
        t += gap

    picks.sort(key=lambda x: x[0])
    return picks[:n]


def _extract_frame(video_path: str, time_s: float, out_path: str) -> bool:
    r = subprocess.run([
        "ffmpeg", "-loglevel", "error",
        "-ss", str(time_s),
        "-i", video_path,
        "-vframes", "1",
        "-q:v", "2",   # kualitas tinggi
        "-y", out_path,
    ], capture_output=True)
    return r.returncode == 0 and os.path.exists(out_path)


def _detect_face_quality(jpeg_path: str) -> tuple[float, int]:
    """Kembalikan (best_confidence, face_count) dari frame."""
    try:
        from deepface import DeepFace
        import cv2
        frame = cv2.imread(jpeg_path)
        if frame is None:
            return 0.3, 0
        results = DeepFace.analyze(
            frame, actions=["emotion"],
            enforce_detection=False, silent=True,
        )
        if isinstance(results, dict):
            results = [results]
        confs = [r.get("face_confidence", 0) for r in results
                 if r.get("face_confidence", 0) >= _MIN_FACE_CONF]
        return (max(confs) if confs else 0.3), len(confs)
    except Exception:
        return 0.3, 0
