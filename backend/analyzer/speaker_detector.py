"""Speaker Detector — deteksi siapa yang sedang berbicara per segmen waktu.

Metode: Lip Aspect Ratio (LAR) via MediaPipe FaceLandmarker (Tasks API),
dikorelasikan dengan audio RMS dari audio analyzer.

Output: list[SpeakerSegment]
"""

from __future__ import annotations

import logging
import os
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from backend.analyzer.person_tracker import BBox, PersonTrack

logger = logging.getLogger(__name__)

_SEGMENT_S     = 0.5
_SAMPLE_FPS    = 10
_LAR_THRESHOLD = 0.025
_ENERGY_THRESH = 0.01

# Landmark index untuk bibir (MediaPipe FaceMesh 478 points, Tasks API)
_LIP_TOP_IDX    = 13   # upper inner lip
_LIP_BOTTOM_IDX = 14   # lower inner lip

_MODEL_DIR  = Path(__file__).parent.parent.parent / "tmp" / "mp_models"
_MODEL_PATH = _MODEL_DIR / "face_landmarker.task"
_MODEL_URL  = (
    "https://storage.googleapis.com/mediapipe-models/"
    "face_landmarker/face_landmarker/float16/1/face_landmarker.task"
)


def _ensure_model() -> str:
    """Download face landmarker model jika belum ada."""
    _MODEL_DIR.mkdir(parents=True, exist_ok=True)
    if not _MODEL_PATH.exists():
        logger.info("Downloading face_landmarker.task (~3.6MB)...")
        urllib.request.urlretrieve(_MODEL_URL, _MODEL_PATH)
        logger.info("Model downloaded: %s", _MODEL_PATH)
    return str(_MODEL_PATH)


@dataclass
class SpeakerSegment:
    t_start: float
    t_end: float
    speaker_id: str
    confidence: float
    all_scores: dict[str, float]


def detect_speakers(
    video_path: str,
    tracks: list[PersonTrack],
    audio_rms: list[dict] | None = None,
) -> list[SpeakerSegment]:
    if not tracks:
        return []

    try:
        import mediapipe as mp
        VisionRunningMode = mp.tasks.vision.RunningMode
        FaceLandmarker    = mp.tasks.vision.FaceLandmarker
        FaceLandmarkerOpts = mp.tasks.vision.FaceLandmarkerOptions
        BaseOptions       = mp.tasks.BaseOptions
        MPImage           = mp.Image
        MPImageFormat     = mp.ImageFormat
    except ImportError:
        logger.error("mediapipe tidak terinstall")
        return []

    model_path = _ensure_model()

    def _make_landmarker(delegate):
        return FaceLandmarker.create_from_options(FaceLandmarkerOpts(
            base_options=BaseOptions(
                model_asset_path=model_path,
                delegate=delegate,
            ),
            running_mode=VisionRunningMode.IMAGE,
            num_faces=min(len(tracks) + 2, 6),
            min_face_detection_confidence=0.4,
            min_face_presence_confidence=0.4,
        ))

    try:
        landmarker = _make_landmarker(BaseOptions.Delegate.GPU)
        logger.info("Speaker detector: pakai GPU delegate")
    except Exception as e:
        logger.warning("GPU delegate gagal (%s), fallback ke CPU", e)
        landmarker = _make_landmarker(BaseOptions.Delegate.CPU)

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        landmarker.close()
        return []

    fps      = cap.get(cv2.CAP_PROP_FPS) or 25.0
    total    = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    vid_w    = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    vid_h    = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    duration = total / fps
    step     = max(1, int(fps / _SAMPLE_FPS))

    # Build RMS lookup
    rms_lookup: dict[float, float] = {}
    if audio_rms:
        for entry in audio_rms:
            t_key = round(float(entry.get("t", entry.get("time", 0))), 1)
            rms_lookup[t_key] = float(entry.get("rms", entry.get("energy", 0)))

    def get_rms(t: float) -> float:
        if not rms_lookup:
            return 1.0
        key = round(round(t / _SEGMENT_S) * _SEGMENT_S, 1)
        return rms_lookup.get(key, 0.0)

    # lar_data[person_id] = [(t, lar_value), ...]
    lar_data: dict[str, list[tuple[float, float]]] = {
        tr.person_id: [] for tr in tracks
    }

    frame_idx = 0
    while True:
        ret, frame = cap.read()
        if not ret:
            break

        if frame_idx % step == 0:
            t = frame_idx / fps
            frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            mp_image  = MPImage(image_format=MPImageFormat.SRGB, data=frame_rgb)

            result = landmarker.detect(mp_image)

            if result.face_landmarks:
                for face_lms in result.face_landmarks:
                    # Approximate wajah center
                    xs = [lm.x * vid_w for lm in face_lms]
                    ys = [lm.y * vid_h for lm in face_lms]
                    face_cx = int(np.mean(xs))
                    face_cy = int(np.mean(ys))

                    best_track = _match_track(face_cx, face_cy, t, tracks)
                    if best_track is None:
                        continue

                    upper = face_lms[_LIP_TOP_IDX]
                    lower = face_lms[_LIP_BOTTOM_IDX]
                    lar   = abs(upper.y - lower.y)   # normalized [0,1]

                    lar_data[best_track.person_id].append((t, lar))

        frame_idx += 1

    cap.release()
    landmarker.close()

    # Build speaker segments
    segments: list[SpeakerSegment] = []
    n_segs = int(duration / _SEGMENT_S) + 1

    for seg_i in range(n_segs):
        t_start = seg_i * _SEGMENT_S
        t_end   = t_start + _SEGMENT_S
        rms     = get_rms(t_start)

        scores: dict[str, float] = {}
        for pid, entries in lar_data.items():
            in_seg = [lar for t, lar in entries if t_start <= t < t_end]
            scores[pid] = float(np.mean(in_seg)) if in_seg else 0.0

        if not scores or rms < _ENERGY_THRESH:
            segments.append(SpeakerSegment(
                t_start=t_start, t_end=t_end,
                speaker_id="none", confidence=0.0,
                all_scores=scores,
            ))
            continue

        best_pid = max(scores, key=scores.get)
        best_lar = scores[best_pid]

        if best_lar < _LAR_THRESHOLD:
            segments.append(SpeakerSegment(
                t_start=t_start, t_end=t_end,
                speaker_id="none", confidence=0.0,
                all_scores={k: round(v, 4) for k, v in scores.items()},
            ))
        else:
            conf = min(1.0, (best_lar / _LAR_THRESHOLD) * min(rms * 5, 1.0))
            segments.append(SpeakerSegment(
                t_start=t_start, t_end=t_end,
                speaker_id=best_pid,
                confidence=round(conf, 3),
                all_scores={k: round(v, 4) for k, v in scores.items()},
            ))

    logger.info("Speaker detector selesai: %d segmen", len(segments))
    return segments


def _match_track(
    face_cx: int, face_cy: int, t: float,
    tracks: list[PersonTrack],
    max_dist: int = 200,
) -> PersonTrack | None:
    best = None
    best_dist = max_dist
    for track in tracks:
        bbox = track.bbox_at(t)
        if bbox is None:
            continue
        dist = ((bbox.cx - face_cx) ** 2 + (bbox.cy - face_cy) ** 2) ** 0.5
        if dist < best_dist:
            best_dist = dist
            best = track
    return best


def segments_to_dict(segments: list[SpeakerSegment]) -> list[dict]:
    return [
        {
            "t_start":   s.t_start,
            "t_end":     s.t_end,
            "speaker_id": s.speaker_id,
            "confidence": s.confidence,
            "all_scores": s.all_scores,
        }
        for s in segments
    ]
