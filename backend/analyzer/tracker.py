"""Dynamic face tracking crop — crop window mengikuti wajah setiap frame.

Pipeline:
  1. DeepFace detect wajah di frame pertama → init CSRT tracker
  2. CSRT track setiap frame (~5ms/frame), DeepFace re-detect tiap N frame
  3. Smooth trajectory pakai EMA agar tidak jittery
  4. Tiap frame di-crop lalu di-pipe ke FFmpeg → preserve audio asli

Multi-person: tiap orang punya tracker sendiri → split grid output.
"""

from __future__ import annotations

import logging
import os
import subprocess
from dataclasses import dataclass, field
from typing import Callable

import cv2

logger = logging.getLogger(__name__)

# Interval re-detect dengan DeepFace (lebih tinggi = lebih cepat, bisa drift)
_DETECT_INTERVAL  = 15
# EMA smoothing factor: 0.0 = tidak smooth, 1.0 = tidak gerak sama sekali
_SMOOTH_ALPHA     = 0.85
# Toleransi pergeseran wajah per frame (px) sebelum dianggap jump
_MAX_SHIFT_PX     = 60
_MIN_FACE_CONF    = 0.60


@dataclass
class TrackState:
    tracker: object          # cv2.Tracker
    cx: float                # center x saat ini (smooth)
    cy: float                # center y saat ini (smooth)
    w: float                 # face width estimate
    h: float                 # face height estimate
    lost: int = 0            # berapa frame terakhir tracker gagal


@dataclass
class TrackResult:
    output_path: str
    strategy: str            # "single" | "grid_h2" | "grid_v2" | "grid_h3"
    people_count: int
    frames_processed: int
    duration_s: float


def track_and_crop(
    video_path: str,
    output_path: str,
    target_w: int = 1080,
    target_h: int = 1920,
    detect_interval: int = _DETECT_INTERVAL,
    progress_cb: Callable[[int, int], None] | None = None,
) -> TrackResult:
    """Tracking crop untuk video. Hasilnya MP4 dengan audio preserved.

    progress_cb(current_frame, total_frames) dipanggil tiap 30 frame.
    """
    try:
        from deepface import DeepFace
    except ImportError as e:
        raise RuntimeError(f"deepface tidak tersedia: {e}") from e

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise RuntimeError(f"Tidak bisa buka: {video_path}")

    fps         = cap.get(cv2.CAP_PROP_FPS) or 24.0
    total       = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    vid_w       = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    vid_h       = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

    # Tentukan strategi dari beberapa frame awal
    strategy, n_people = _detect_initial_strategy(cap, DeepFace, vid_w, vid_h)
    logger.info("Track strategy: %s, people: %d", strategy, n_people)

    # Hitung ukuran panel output berdasarkan strategi
    panels_out = _panel_sizes(strategy, target_w, target_h)

    # Init trackers (satu per orang)
    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
    ret, first_frame = cap.read()
    if not ret:
        cap.release()
        raise RuntimeError("Tidak bisa baca frame pertama")

    states = _init_trackers(first_frame, DeepFace, n_people, vid_w, vid_h, strategy)

    # FFmpeg pipe — terima raw BGR frames dari stdin, merge audio dari original
    pipe_cmd = _build_ffmpeg_pipe(
        video_path, output_path, panels_out, fps, strategy
    )
    ffmpeg_proc = subprocess.Popen(
        pipe_cmd, stdin=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )

    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
    frame_idx = 0

    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                break

            # Re-detect tiap interval atau saat tracker lost
            if frame_idx % detect_interval == 0 or any(s.lost > 5 for s in states):
                _redetect(frame, DeepFace, states, vid_w, vid_h, strategy)

            # Update semua tracker
            for s in states:
                _update_tracker(s, frame)

            # Crop frame menjadi panels
            panels_frames = _crop_panels(frame, states, panels_out, vid_w, vid_h, strategy)

            # Compose final frame
            composed = _compose(panels_frames, panels_out, target_w, target_h, strategy)

            # Write ke FFmpeg pipe
            ffmpeg_proc.stdin.write(composed.tobytes())

            if progress_cb and frame_idx % 30 == 0:
                progress_cb(frame_idx, total)

            frame_idx += 1

    finally:
        cap.release()
        try:
            ffmpeg_proc.stdin.close()
            ffmpeg_proc.wait(timeout=30)
        except Exception:
            ffmpeg_proc.kill()

    return TrackResult(
        output_path=output_path,
        strategy=strategy,
        people_count=n_people,
        frames_processed=frame_idx,
        duration_s=round(frame_idx / fps, 2),
    )


# ---------------------------------------------------------------- strategy

def _detect_initial_strategy(cap, DeepFace, vid_w: int, vid_h: int) -> tuple[str, int]:
    """Sample 5 frame awal untuk tentukan strategi."""
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    counts = []
    for idx in [0, total//5, total//3, total//2, 2*total//3]:
        cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
        ret, frame = cap.read()
        if not ret:
            continue
        faces = _detect_faces_raw(frame, DeepFace)
        counts.append(len(faces))

    n = max(1, min(3, int(sorted(counts)[len(counts)//2]))) if counts else 1

    if n == 1:
        return "single", 1
    if n == 2:
        # Cek apakah berdampingan horizontal atau vertikal
        cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
        _, frame = cap.read()
        faces = _detect_faces_raw(frame, DeepFace)
        if len(faces) >= 2:
            faces.sort(key=lambda f: f[0] + f[2]//2)  # sort by cx
            cy_diff = abs((faces[0][1] + faces[0][3]//2) - (faces[1][1] + faces[1][3]//2))
            if cy_diff < vid_h * 0.20:
                return "grid_h2", 2
            return "grid_v2", 2
        return "single", 1
    return "grid_h3", 3


def _panel_sizes(strategy: str, out_w: int, out_h: int) -> list[tuple[int, int]]:
    if strategy == "single":
        return [(out_w, out_h)]
    if strategy == "grid_h2":
        return [(out_w // 2, out_h), (out_w // 2, out_h)]
    if strategy == "grid_v2":
        return [(out_w, out_h // 2), (out_w, out_h // 2)]
    if strategy == "grid_h3":
        return [(out_w // 3, out_h)] * 3
    return [(out_w, out_h)]


# ---------------------------------------------------------------- tracking

def _init_trackers(
    frame, DeepFace, n: int, vid_w: int, vid_h: int, strategy: str
) -> list[TrackState]:
    faces = _detect_faces_raw(frame, DeepFace)
    faces.sort(key=lambda f: f[0] + f[2]//2)  # sort kiri→kanan
    states = []

    for i in range(n):
        if i < len(faces):
            x, y, w, h = faces[i]
        else:
            # Fallback: bagi frame secara merata
            x = vid_w * i // n
            y = vid_h // 4
            w = vid_w // n
            h = vid_h // 2

        tracker = cv2.TrackerCSRT_create()
        tracker.init(frame, (x, y, w, h))
        states.append(TrackState(
            tracker=tracker,
            cx=float(x + w // 2),
            cy=float(y + h // 2),
            w=float(w), h=float(h),
        ))
    return states


def _update_tracker(state: TrackState, frame) -> None:
    """Update tracker dan smooth posisi dengan EMA."""
    ok, bbox = state.tracker.update(frame)
    if ok and bbox[2] > 0 and bbox[3] > 0:
        nx, ny, nw, nh = [float(v) for v in bbox]
        new_cx = nx + nw / 2
        new_cy = ny + nh / 2
        # Hanya smooth kalau pergerakan masuk akal (bukan jump)
        shift = ((new_cx - state.cx)**2 + (new_cy - state.cy)**2) ** 0.5
        if shift < _MAX_SHIFT_PX:
            state.cx = _SMOOTH_ALPHA * state.cx + (1 - _SMOOTH_ALPHA) * new_cx
            state.cy = _SMOOTH_ALPHA * state.cy + (1 - _SMOOTH_ALPHA) * new_cy
            state.w  = _SMOOTH_ALPHA * state.w  + (1 - _SMOOTH_ALPHA) * nw
            state.h  = _SMOOTH_ALPHA * state.h  + (1 - _SMOOTH_ALPHA) * nh
        state.lost = 0
    else:
        state.lost += 1


def _redetect(frame, DeepFace, states: list[TrackState], vid_w: int, vid_h: int, strategy: str) -> None:
    """Re-deteksi wajah dan reset tracker yang sesuai."""
    faces = _detect_faces_raw(frame, DeepFace)
    if not faces:
        return
    faces.sort(key=lambda f: f[0] + f[2]//2)

    for i, state in enumerate(states):
        if i < len(faces):
            x, y, w, h = faces[i]
            new_cx = float(x + w // 2)
            new_cy = float(y + h // 2)
            # Hanya reset tracker jika posisi berbeda signifikan
            shift = ((new_cx - state.cx)**2 + (new_cy - state.cy)**2) ** 0.5
            if shift > 20 or state.lost > 3:
                state.tracker = cv2.TrackerCSRT_create()
                state.tracker.init(frame, (x, y, w, h))
                # Smooth re-entry
                state.cx = 0.5 * state.cx + 0.5 * new_cx
                state.cy = 0.5 * state.cy + 0.5 * new_cy
                state.w  = 0.5 * state.w  + 0.5 * float(w)
                state.h  = 0.5 * state.h  + 0.5 * float(h)
                state.lost = 0


# ---------------------------------------------------------------- crop & compose

def _crop_panels(
    frame, states: list[TrackState],
    panels_out: list[tuple[int, int]],
    vid_w: int, vid_h: int,
    strategy: str,
) -> list:
    panels = []
    for i, (state, (pw, ph)) in enumerate(zip(states, panels_out)):
        # Hitung crop region dengan aspect ratio panel output
        aspect = pw / ph
        crop_h = min(vid_h, int(vid_w / aspect))
        crop_w = min(vid_w, int(crop_h * aspect))

        # Center crop pada posisi wajah yang sudah di-smooth
        cx = int(state.cx)
        cy = int(state.cy + crop_h * 0.08)  # sedikit headroom atas

        sx = max(0, min(vid_w - crop_w, cx - crop_w // 2))
        sy = max(0, min(vid_h - crop_h, cy - crop_h // 2))

        cropped = frame[sy:sy+crop_h, sx:sx+crop_w]
        resized = cv2.resize(cropped, (pw, ph), interpolation=cv2.INTER_LINEAR)
        panels.append(resized)

    return panels


def _compose(panels: list, panels_out: list[tuple[int, int]], out_w: int, out_h: int, strategy: str):
    if strategy == "single" or len(panels) == 1:
        return panels[0]
    if strategy == "grid_h2" and len(panels) >= 2:
        return cv2.hconcat(panels[:2])
    if strategy == "grid_v2" and len(panels) >= 2:
        return cv2.vconcat(panels[:2])
    if strategy == "grid_h3" and len(panels) >= 3:
        return cv2.hconcat(panels[:3])
    return panels[0]


# ---------------------------------------------------------------- helpers

def _detect_faces_raw(frame, DeepFace) -> list[tuple[int, int, int, int]]:
    """Kembalikan list (x, y, w, h) dari frame."""
    try:
        results = DeepFace.analyze(
            frame, actions=["emotion"],
            enforce_detection=False, silent=True,
        )
        if isinstance(results, dict):
            results = [results]
        boxes = []
        for r in results:
            if r.get("face_confidence", 0) < _MIN_FACE_CONF:
                continue
            reg = r.get("region", {})
            if reg and reg.get("w", 0) > 0:
                boxes.append((
                    int(reg["x"]), int(reg["y"]),
                    int(reg["w"]), int(reg["h"]),
                ))
        return boxes
    except Exception:
        return []


def _build_ffmpeg_pipe(
    src_video: str, output_path: str,
    panels_out: list[tuple[int, int]],
    fps: float,
    strategy: str,
) -> list[str]:
    """FFmpeg command yang baca raw BGR frames dari stdin + audio dari src_video."""
    # Hitung total output size dari strategy
    if strategy == "grid_h2":
        final_w = panels_out[0][0] * 2
        final_h = panels_out[0][1]
    elif strategy == "grid_v2":
        final_w = panels_out[0][0]
        final_h = panels_out[0][1] * 2
    elif strategy == "grid_h3":
        final_w = panels_out[0][0] * 3
        final_h = panels_out[0][1]
    else:
        final_w, final_h = panels_out[0]

    return [
        "ffmpeg", "-y", "-loglevel", "error",
        # Raw video dari stdin
        "-f", "rawvideo", "-pix_fmt", "bgr24",
        "-s", f"{final_w}x{final_h}",
        "-r", str(fps),
        "-i", "pipe:0",
        # Audio dari original
        "-i", src_video,
        "-map", "0:v",
        "-map", "1:a?",          # audio opsional (kalau ada)
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        output_path,
    ]
