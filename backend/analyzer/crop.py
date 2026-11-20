"""Smart crop analyzer — deteksi posisi wajah lalu generate strategi grid.

Strategi:
  single      → 1 orang, crop 9:16 centered pada wajah
  grid_h2     → 2 orang, split horizontal (kiri|kanan), masing-masing 540px wide
  grid_v2     → 2 orang atas/bawah, masing-masing setengah tinggi
  grid_h3     → 3 orang, tiga panel horizontal
  wide        → fallback: center crop seluruh frame
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from statistics import median
from typing import Literal

logger = logging.getLogger(__name__)

Strategy = Literal["single", "grid_h2", "grid_v2", "grid_h3", "wide"]

# Berapa frame yang di-sample untuk analisis crop
_SAMPLE_FRAMES = 20
_MIN_FACE_CONFIDENCE = 0.50  # lebih rendah agar wajah kecil di multi-person terdeteksi


@dataclass
class FaceBox:
    x: int; y: int; w: int; h: int
    confidence: float = 0.9

    @property
    def cx(self) -> int:
        return self.x + self.w // 2

    @property
    def cy(self) -> int:
        return self.y + self.h // 2


@dataclass
class CropPanel:
    person_id: int
    src_x: int; src_y: int
    src_w: int; src_h: int   # region dari original frame yang di-crop
    out_w: int; out_h: int   # ukuran output panel


@dataclass
class CropResult:
    strategy: Strategy
    people_count: int
    video_w: int
    video_h: int
    panels: list[CropPanel] = field(default_factory=list)
    ffmpeg_filter: str = ""
    confidence: float = 0.0


def analyze_crop(
    video_path: str,
    target_w: int = 1080,
    target_h: int = 1920,
) -> CropResult:
    """Analisis video dan kembalikan strategi crop terbaik.

    target_w/target_h = resolusi output yang diinginkan (default 9:16 1080p).
    ffmpeg_filter siap pakai di -vf atau -filter_complex.
    """
    try:
        import cv2
        from deepface import DeepFace
    except ImportError as e:
        raise RuntimeError(f"Library tidak tersedia: {e}") from e

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise RuntimeError(f"Tidak bisa buka video: {video_path}")

    fps         = cap.get(cv2.CAP_PROP_FPS) or 24.0
    total       = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    vid_w       = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    vid_h       = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap.release()

    # Sample frame indices tersebar merata
    step = max(1, total // _SAMPLE_FRAMES)
    sample_indices = list(range(0, total, step))[:_SAMPLE_FRAMES]

    all_faces: list[list[FaceBox]] = []  # per frame

    cap = cv2.VideoCapture(video_path)
    for idx in sample_indices:
        cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
        ret, frame = cap.read()
        if not ret:
            continue
        faces = _detect_faces(frame, DeepFace)
        if faces:
            all_faces.append(faces)
    cap.release()

    if not all_faces:
        return _fallback(vid_w, vid_h, target_w, target_h)

    # Estimasi jumlah orang konsisten
    people_count = _estimate_people_count(all_faces)
    # Hitung posisi median tiap orang
    median_faces = _median_positions(all_faces, people_count, vid_w, vid_h)

    result = _build_strategy(median_faces, people_count, vid_w, vid_h, target_w, target_h)
    return result


# ---------------------------------------------------------------- internal

def _detect_faces(frame, DeepFace) -> list[FaceBox]:
    try:
        import cv2
        # Upscale frame kecil agar wajah lebih mudah terdeteksi
        orig_h, orig_w = frame.shape[:2]
        scale = 1.0
        if orig_w < 1280:
            scale = 1280 / orig_w
            frame = cv2.resize(frame, (int(orig_w * scale), int(orig_h * scale)))
        results = DeepFace.analyze(
            frame, actions=["emotion"],
            enforce_detection=False, silent=True,
        )
        if isinstance(results, dict):
            results = [results]

        boxes = []
        for r in results:
            conf = r.get("face_confidence", 0)
            if conf < _MIN_FACE_CONFIDENCE:
                continue
            region = r.get("region", {})
            if not region:
                continue
            # Scale balik ke koordinat frame original
            boxes.append(FaceBox(
                x=int(region.get("x", 0) / scale),
                y=int(region.get("y", 0) / scale),
                w=int(region.get("w", 0) / scale),
                h=int(region.get("h", 0) / scale),
                confidence=round(conf, 3),
            ))
        # sort kiri → kanan agar person ID konsisten antar frame
        boxes.sort(key=lambda b: b.cx)
        return boxes
    except Exception as exc:
        logger.debug("Face detection skip: %s", exc)
        return []


def _estimate_people_count(all_faces: list[list[FaceBox]]) -> int:
    counts = [len(f) for f in all_faces if f]
    if not counts:
        return 1
    return max(1, min(6, int(median(counts))))


def _median_positions(
    all_faces: list[list[FaceBox]],
    n_people: int,
    vid_w: int,
    vid_h: int,
) -> list[FaceBox]:
    """Ambil posisi median tiap orang (slot 0..n_people-1 sorted kiri→kanan)."""
    slots: list[list[FaceBox]] = [[] for _ in range(n_people)]
    for frame_faces in all_faces:
        # Ambil hingga n_people face, sort kiri→kanan
        candidates = sorted(frame_faces, key=lambda b: b.cx)[:n_people]
        for slot_idx, face in enumerate(candidates):
            slots[slot_idx].append(face)

    result = []
    for slot in slots:
        if not slot:
            continue
        cx = int(median(f.cx for f in slot))
        cy = int(median(f.cy for f in slot))
        w  = int(median(f.w  for f in slot))
        h  = int(median(f.h  for f in slot))
        # Clamp agar tidak keluar batas video
        cx = max(w // 2, min(vid_w - w // 2, cx))
        cy = max(h // 2, min(vid_h - h // 2, cy))
        result.append(FaceBox(x=cx - w//2, y=cy - h//2, w=w, h=h))
    return result


def _build_strategy(
    faces: list[FaceBox],
    n: int,
    vid_w: int,
    vid_h: int,
    out_w: int,
    out_h: int,
) -> CropResult:
    if n == 1 or len(faces) == 1:
        return _strategy_single(faces[0], vid_w, vid_h, out_w, out_h)

    if n == 2 and len(faces) >= 2:
        a, b = faces[0], faces[1]
        # Jika dua wajah berdampingan secara horizontal (selisih Y < 20% tinggi video)
        if abs(a.cy - b.cy) < vid_h * 0.20:
            return _strategy_grid_h2(a, b, vid_w, vid_h, out_w, out_h)
        else:
            return _strategy_grid_v2(a, b, vid_w, vid_h, out_w, out_h)

    if n == 3 and len(faces) >= 3:
        return _strategy_grid_h3(faces[:3], vid_w, vid_h, out_w, out_h)

    # 4-6 orang → wide crop, tampilkan semua
    result = _fallback(vid_w, vid_h, out_w, out_h)
    result.people_count = n
    return result


def _strategy_single(
    face: FaceBox, vid_w: int, vid_h: int, out_w: int, out_h: int
) -> CropResult:
    """Crop 9:16 centered pada satu wajah, dengan headroom di atas."""
    crop_w = min(vid_w, int(vid_h * out_w / out_h))
    crop_h = min(vid_h, int(vid_w * out_h / out_w))
    crop_w = min(crop_w, vid_w)
    crop_h = min(crop_h, vid_h)

    # Posisikan wajah di 1/3 atas crop (headroom)
    cx = face.cx
    cy = face.cy + crop_h // 6  # geser ke bawah sedikit biar ada headroom
    sx = max(0, min(vid_w - crop_w, cx - crop_w // 2))
    sy = max(0, min(vid_h - crop_h, cy - crop_h // 2))

    panel = CropPanel(0, sx, sy, crop_w, crop_h, out_w, out_h)
    vf = (
        f"crop={crop_w}:{crop_h}:{sx}:{sy},"
        f"scale={out_w}:{out_h}"
    )
    return CropResult(
        strategy="single", people_count=1,
        video_w=vid_w, video_h=vid_h,
        panels=[panel], ffmpeg_filter=vf,
        confidence=face.confidence,
    )


def _strategy_grid_h2(
    a: FaceBox, b: FaceBox,
    vid_w: int, vid_h: int, out_w: int, out_h: int,
) -> CropResult:
    """2 panel horizontal (kiri | kanan), output out_w × out_h total."""
    half_w = out_w // 2
    # Tiap panel: half_w × out_h → crop region proporsional di source
    crop_panel_w = min(vid_w // 2, int(vid_h * half_w / out_h))
    crop_panel_h = min(vid_h, int(crop_panel_w * out_h / half_w))

    def _panel_crop(face: FaceBox, pid: int) -> tuple[CropPanel, str]:
        cx = max(crop_panel_w // 2, min(vid_w - crop_panel_w // 2, face.cx))
        cy = max(crop_panel_h // 2, min(vid_h - crop_panel_h // 2, face.cy + crop_panel_h // 6))
        sx = max(0, min(vid_w - crop_panel_w, cx - crop_panel_w // 2))
        sy = max(0, min(vid_h - crop_panel_h, cy - crop_panel_h // 2))
        return CropPanel(pid, sx, sy, crop_panel_w, crop_panel_h, half_w, out_h), f"{sx}:{sy}"

    pa, pos_a = _panel_crop(a, 0)
    pb, pos_b = _panel_crop(b, 1)

    vf = (
        f"[0:v]crop={crop_panel_w}:{crop_panel_h}:{pos_a},scale={half_w}:{out_h}[left];"
        f"[0:v]crop={crop_panel_w}:{crop_panel_h}:{pos_b},scale={half_w}:{out_h}[right];"
        f"[left][right]hstack=inputs=2[out]"
    )
    return CropResult(
        strategy="grid_h2", people_count=2,
        video_w=vid_w, video_h=vid_h,
        panels=[pa, pb], ffmpeg_filter=vf,
        confidence=min(a.confidence, b.confidence),
    )


def _strategy_grid_v2(
    a: FaceBox, b: FaceBox,
    vid_w: int, vid_h: int, out_w: int, out_h: int,
) -> CropResult:
    """2 panel vertikal (atas | bawah), 1 orang per panel."""
    half_h = out_h // 2
    crop_panel_h = min(vid_h // 2, int(vid_w * half_h / out_w))
    crop_panel_w = min(vid_w, int(crop_panel_h * out_w / half_h))

    def _panel_crop(face: FaceBox, pid: int) -> tuple[CropPanel, str]:
        cx = max(crop_panel_w // 2, min(vid_w - crop_panel_w // 2, face.cx))
        cy = max(crop_panel_h // 2, min(vid_h - crop_panel_h // 2, face.cy))
        sx = max(0, min(vid_w - crop_panel_w, cx - crop_panel_w // 2))
        sy = max(0, min(vid_h - crop_panel_h, cy - crop_panel_h // 2))
        return CropPanel(pid, sx, sy, crop_panel_w, crop_panel_h, out_w, half_h), f"{sx}:{sy}"

    pa, pos_a = _panel_crop(a, 0)
    pb, pos_b = _panel_crop(b, 1)

    vf = (
        f"[0:v]crop={crop_panel_w}:{crop_panel_h}:{pos_a},scale={out_w}:{half_h}[top];"
        f"[0:v]crop={crop_panel_w}:{crop_panel_h}:{pos_b},scale={out_w}:{half_h}[bot];"
        f"[top][bot]vstack=inputs=2[out]"
    )
    return CropResult(
        strategy="grid_v2", people_count=2,
        video_w=vid_w, video_h=vid_h,
        panels=[pa, pb], ffmpeg_filter=vf,
        confidence=min(a.confidence, b.confidence),
    )


def _strategy_grid_h3(
    faces: list[FaceBox],
    vid_w: int, vid_h: int, out_w: int, out_h: int,
) -> CropResult:
    """3 panel horizontal (kiri | tengah | kanan)."""
    third_w = out_w // 3
    crop_pw  = min(vid_w // 3, int(vid_h * third_w / out_h))
    crop_ph  = min(vid_h, int(crop_pw * out_h / third_w))

    panels, parts = [], []
    labels = ["left", "mid", "right"]
    for i, (face, label) in enumerate(zip(faces, labels)):
        cx = max(crop_pw // 2, min(vid_w - crop_pw // 2, face.cx))
        cy = max(crop_ph // 2, min(vid_h - crop_ph // 2, face.cy + crop_ph // 6))
        sx = max(0, min(vid_w - crop_pw, cx - crop_pw // 2))
        sy = max(0, min(vid_h - crop_ph, cy - crop_ph // 2))
        panels.append(CropPanel(i, sx, sy, crop_pw, crop_ph, third_w, out_h))
        parts.append(f"[0:v]crop={crop_pw}:{crop_ph}:{sx}:{sy},scale={third_w}:{out_h}[{label}]")

    vf = ";".join(parts) + ";[left][mid][right]hstack=inputs=3[out]"
    return CropResult(
        strategy="grid_h3", people_count=3,
        video_w=vid_w, video_h=vid_h,
        panels=panels, ffmpeg_filter=vf,
        confidence=min(f.confidence for f in faces),
    )


def _fallback(vid_w: int, vid_h: int, out_w: int, out_h: int) -> CropResult:
    """Center crop tanpa face detection."""
    crop_w = min(vid_w, int(vid_h * out_w / out_h))
    crop_h = min(vid_h, int(vid_w * out_h / out_w))
    sx = (vid_w - crop_w) // 2
    sy = (vid_h - crop_h) // 2
    panel = CropPanel(0, sx, sy, crop_w, crop_h, out_w, out_h)
    vf = f"crop={crop_w}:{crop_h}:{sx}:{sy},scale={out_w}:{out_h}"
    return CropResult(
        strategy="wide", people_count=0,
        video_w=vid_w, video_h=vid_h,
        panels=[panel], ffmpeg_filter=vf,
        confidence=0.0,
    )
