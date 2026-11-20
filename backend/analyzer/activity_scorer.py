"""Activity Scorer — gabungkan expression + motion + speaker jadi satu skor.

Output: dict[person_id → list[ActivityPoint]] — skor per 0.5 detik.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import cv2
import numpy as np

from backend.analyzer.person_tracker import PersonTrack
from backend.analyzer.speaker_detector import SpeakerSegment

logger = logging.getLogger(__name__)

_SEGMENT_S = 0.5
_W_EXPRESSION = 0.40
_W_MOTION     = 0.30
_W_SPEAKER    = 0.30

_EXPRESSION_SCORES = {
    "happy": 1.0, "surprise": 0.9, "angry": 0.8,
    "fear": 0.6,  "sad": 0.4,     "disgust": 0.5,
    "neutral": 0.0,
}


@dataclass
class ActivityPoint:
    t: float
    expression_score: float
    motion_score: float
    speaker_score: float
    total: float
    is_speaker: bool


def score_activity(
    video_path: str,
    tracks: list[PersonTrack],
    speaker_segments: list[SpeakerSegment],
    deepface_results: list[dict] | None = None,  # dari expressions analyzer
) -> dict[str, list[ActivityPoint]]:
    """Hitung activity score per orang per segmen waktu."""

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return {}

    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    vid_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    vid_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    duration = total / fps
    step = max(1, int(fps / 4))  # 4 fps untuk motion

    # Lookup speaker: t → speaker_id
    def get_speaker(t: float) -> str:
        for seg in speaker_segments:
            if seg.t_start <= t < seg.t_end:
                return seg.speaker_id
        return "none"

    # Hitung optical flow motion score per person per frame
    motion_data: dict[str, list[tuple[float, float]]] = {
        t.person_id: [] for t in tracks
    }

    prev_gray: np.ndarray | None = None
    frame_idx = 0

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        if frame_idx % step == 0:
            t = frame_idx / fps
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

            if prev_gray is not None:
                for track in tracks:
                    bbox = track.bbox_at(t)
                    if bbox is None:
                        continue

                    # Crop ROI untuk orang ini
                    x1 = max(0, bbox.x); y1 = max(0, bbox.y)
                    x2 = min(vid_w, bbox.x + bbox.w)
                    y2 = min(vid_h, bbox.y + bbox.h)
                    if x2 <= x1 or y2 <= y1:
                        continue

                    roi_cur  = gray[y1:y2, x1:x2]
                    roi_prev = prev_gray[y1:y2, x1:x2]

                    if roi_cur.shape != roi_prev.shape or roi_cur.size == 0:
                        continue

                    # Farneback optical flow
                    flow = cv2.calcOpticalFlowFarneback(
                        roi_prev, roi_cur, None,
                        0.5, 3, 15, 3, 5, 1.2, 0,
                    )
                    magnitude = np.sqrt(flow[..., 0]**2 + flow[..., 1]**2)
                    score = float(np.mean(magnitude))
                    motion_data[track.person_id].append((t, score))

            prev_gray = gray

        frame_idx += 1

    cap.release()

    # Normalize motion scores ke [0, 1]
    all_motions = [v for vals in motion_data.values() for _, v in vals]
    max_motion = max(all_motions) if all_motions else 1.0
    if max_motion < 0.1:
        max_motion = 1.0

    # Build expression lookup dari deepface_results jika ada
    # Format deepface: [{t, person_id, dominant_emotion, ...}]
    expr_lookup: dict[tuple[str, float], float] = {}
    if deepface_results:
        for entry in deepface_results:
            pid = entry.get("person_id", "A")
            t   = round(float(entry.get("t", 0)) / _SEGMENT_S) * _SEGMENT_S
            em  = entry.get("dominant_emotion", "neutral")
            expr_lookup[(pid, round(t, 1))] = _EXPRESSION_SCORES.get(em, 0.0)

    # Susun ActivityPoint per person per segmen
    result: dict[str, list[ActivityPoint]] = {t.person_id: [] for t in tracks}
    n_segs = int(duration / _SEGMENT_S) + 1

    for seg_i in range(n_segs):
        t_start = round(seg_i * _SEGMENT_S, 1)
        t_end   = t_start + _SEGMENT_S
        speaker_id = get_speaker(t_start)

        for track in tracks:
            pid = track.person_id

            # Motion score (mean dalam segmen)
            m_vals = [v for t, v in motion_data[pid]
                      if t_start <= t < t_end]
            m_score = float(np.mean(m_vals)) / max_motion if m_vals else 0.0

            # Expression score
            e_key = (pid, t_start)
            e_score = expr_lookup.get(e_key, 0.0)

            # Speaker score
            is_spk = (speaker_id == pid)
            s_score = 1.0 if is_spk else 0.0

            total = (
                _W_EXPRESSION * e_score +
                _W_MOTION     * m_score +
                _W_SPEAKER    * s_score
            )

            result[pid].append(ActivityPoint(
                t=t_start,
                expression_score=round(e_score, 3),
                motion_score=round(m_score, 3),
                speaker_score=s_score,
                total=round(total, 3),
                is_speaker=is_spk,
            ))

    logger.info("Activity scorer selesai: %d orang, %d segmen",
                len(result), n_segs)
    return result


def activity_to_dict(
    activity: dict[str, list[ActivityPoint]],
) -> dict[str, list[dict]]:
    return {
        pid: [
            {
                "t": p.t,
                "expression": p.expression_score,
                "motion": p.motion_score,
                "speaker": p.speaker_score,
                "total": p.total,
                "is_speaker": p.is_speaker,
            }
            for p in points
        ]
        for pid, points in activity.items()
    }
