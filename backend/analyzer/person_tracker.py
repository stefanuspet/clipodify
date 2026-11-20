"""Person Tracker — deteksi dan track orang lintas frame pakai YOLOv8 + ByteTrack.

YOLO class 0 = "person" → mendeteksi seluruh tubuh dari pose apapun
(frontal, samping, 3/4 view), jauh lebih robust dari FaceLandmarker.

ByteTrack built-in YOLO menggunakan Kalman filter untuk ID yang stabil
bahkan saat terjadi camera cut.

Setelah tracking, merge track-track yang merupakan orang yang sama
berdasarkan posisi horizontal (kiri vs kanan frame).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

logger = logging.getLogger(__name__)

_SAMPLE_FPS     = 4      # frame yang di-sample per detik
_MIN_FRAMES     = 10     # minimum frame agar track dianggap valid
_CONF_THRESHOLD = 0.40   # minimum confidence YOLO detection
_CLUSTER_THRESH = 0.40   # max jarak center-X (normalized) untuk merge track

_MODEL_PATH = Path(__file__).parent.parent.parent / "yolov8n.pt"


@dataclass
class BBox:
    x: int; y: int; w: int; h: int
    t: float = 0.0
    conf: float = 1.0

    @property
    def x2(self): return self.x + self.w
    @property
    def y2(self): return self.y + self.h
    @property
    def cx(self): return self.x + self.w // 2
    @property
    def cy(self): return self.y + self.h // 2
    @property
    def area(self): return self.w * self.h


@dataclass
class PersonTrack:
    person_id: str
    frames: list[BBox] = field(default_factory=list)

    @property
    def first_seen(self) -> float:
        return self.frames[0].t if self.frames else 0.0

    @property
    def last_seen(self) -> float:
        return self.frames[-1].t if self.frames else 0.0

    def median_bbox(self) -> BBox:
        xs = [f.x for f in self.frames]; ys = [f.y for f in self.frames]
        ws = [f.w for f in self.frames]; hs = [f.h for f in self.frames]
        return BBox(
            x=int(np.median(xs)), y=int(np.median(ys)),
            w=int(np.median(ws)), h=int(np.median(hs)),
        )

    def bbox_at(self, t: float) -> BBox | None:
        if not self.frames:
            return None
        return min(self.frames, key=lambda f: abs(f.t - t))


def _person_id(idx: int) -> str:
    return chr(ord("A") + idx) if idx < 26 else f"P{idx}"


def _kmeans1d(vals: np.ndarray, k: int, max_iter: int = 20) -> np.ndarray:
    """1D K-means — assign tiap val ke salah satu dari k cluster.

    Inisialisasi dengan membagi range [min, max] jadi k bagian sama rata.
    Return array label (0..k-1) untuk tiap val.
    """
    mn, mx = float(vals.min()), float(vals.max())
    if mx - mn < 1e-6:
        return np.zeros(len(vals), dtype=int)
    # Inisialisasi: bagi range jadi k bagian sama rata
    centers = np.array([mn + (i + 0.5) * (mx - mn) / k for i in range(k)])
    labels = np.zeros(len(vals), dtype=int)
    for _ in range(max_iter):
        dists = np.abs(vals[:, None] - centers[None, :])  # (N, k)
        new_labels = np.argmin(dists, axis=1)
        if np.all(new_labels == labels):
            break
        labels = new_labels
        for c in range(k):
            mask = labels == c
            if mask.any():
                centers[c] = vals[mask].mean()
    return labels


def _auto_k(vals: np.ndarray, max_k: int = 6) -> int:
    """Deteksi jumlah cluster optimal pakai elbow method (inertia-based).

    Mencari k di mana menambah cluster lagi hanya memberi < 15% penurunan inertia.
    """
    n = len(vals)
    if n <= 1:
        return 1
    max_k = min(max_k, n)

    inertias: list[float] = []
    for k in range(1, max_k + 1):
        labels = _kmeans1d(vals, k)
        inertia = 0.0
        for c in range(k):
            mask = labels == c
            if mask.any():
                center = float(vals[mask].mean())
                inertia += float(np.sum((vals[mask] - center) ** 2))
        inertias.append(inertia)

    if inertias[0] < 1e-6:
        return 1

    # Normalized: berapa banyak inertia tersisa dibanding k=1
    norm = [v / inertias[0] for v in inertias]
    for k in range(1, len(norm)):
        drop = norm[k - 1] - norm[k]
        if drop < 0.15:   # < 15% improvement → stop
            return k
    return max_k


def _merge_by_position(tracks: list[PersonTrack], vid_w: int) -> list[PersonTrack]:
    """Merge track yang merupakan orang yang sama berdasarkan posisi horizontal.

    Gunakan elbow method untuk auto-detect jumlah orang, lalu K-means untuk assign.
    """
    if len(tracks) <= 1:
        return tracks

    cx_vals = np.array([t.median_bbox().cx / max(vid_w, 1) for t in tracks])

    k = _auto_k(cx_vals, max_k=6)
    # Minimal 2 orang kalau ada spread cx > 0.15 dan > 1 track
    if k == 1 and len(tracks) > 1 and (cx_vals.max() - cx_vals.min()) > 0.15:
        k = 2

    logger.debug("Merge by position: %d tracks, k=%d, cx=[%.2f-%.2f]",
                 len(tracks), k, cx_vals.min(), cx_vals.max())

    labels = _kmeans1d(cx_vals, k)

    # Bagi tracks per cluster
    clusters: dict[int, list[PersonTrack]] = {}
    for i, lbl in enumerate(labels):
        clusters.setdefault(int(lbl), []).append(tracks[i])

    # Merge tiap cluster jadi satu track, sort cluster by mean cx
    cluster_items = sorted(clusters.items(), key=lambda kv: np.mean([t.median_bbox().cx for t in kv[1]]))
    merged: list[PersonTrack] = []
    for _, cluster in cluster_items:
        if len(cluster) == 1:
            merged.append(cluster[0])
            continue
        all_frames: list[BBox] = []
        for t in cluster:
            all_frames.extend(t.frames)
        all_frames.sort(key=lambda f: f.t)
        merged.append(PersonTrack(
            person_id=cluster[0].person_id,
            frames=all_frames,
        ))

    return merged


def track_persons(video_path: str) -> list[PersonTrack]:
    """Proses video → list PersonTrack pakai YOLOv8 + ByteTrack."""
    from ultralytics import YOLO

    model = YOLO(str(_MODEL_PATH))

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        logger.error("Gagal buka video: %s", video_path)
        return []

    fps   = cap.get(cv2.CAP_PROP_FPS) or 25.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    vid_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    vid_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    step  = max(1, int(fps / _SAMPLE_FPS))

    logger.info("Person tracker (YOLO+ByteTrack): %.1fs %dx%d step=%d",
                total / fps, vid_w, vid_h, step)

    # yolo_id → PersonTrack
    track_map: dict[int, PersonTrack] = {}

    frame_idx = 0
    while True:
        ret, frame = cap.read()
        if not ret:
            break

        if frame_idx % step == 0:
            t = frame_idx / fps

            # ByteTrack built-in: persist=True menjaga ID konsisten
            results = model.track(
                frame,
                classes=[0],
                conf=_CONF_THRESHOLD,
                persist=True,
                verbose=False,
            )

            for r in results:
                if r.boxes is None or r.boxes.id is None:
                    continue
                for box in r.boxes:
                    if box.id is None:
                        continue
                    yolo_id = int(box.id[0])
                    x1, y1, x2, y2 = map(int, box.xyxy[0])
                    conf = float(box.conf[0])
                    bb = BBox(
                        x=max(0, x1),
                        y=max(0, y1),
                        w=min(x2 - x1, vid_w - x1),
                        h=min(y2 - y1, vid_h - y1),
                        t=t,
                        conf=conf,
                    )
                    if bb.w < 10 or bb.h < 20:
                        continue
                    if yolo_id not in track_map:
                        track_map[yolo_id] = PersonTrack(
                            person_id=f"raw_{yolo_id}",
                            frames=[],
                        )
                    track_map[yolo_id].frames.append(bb)

        frame_idx += 1

    cap.release()

    tracks = [t for t in track_map.values() if len(t.frames) >= _MIN_FRAMES]
    tracks = _merge_by_position(tracks, vid_w)
    # Sort kiri → kanan (memudahkan layout: A=kiri, B=kanan)
    tracks.sort(key=lambda t: t.median_bbox().cx)
    for i, t in enumerate(tracks):
        t.person_id = _person_id(i)

    logger.info("Person tracker selesai: %d orang unik", len(tracks))
    for tr in tracks:
        mb = tr.median_bbox()
        logger.info("  Track %s: %d frame, t=%.1f–%.1f, cx=%d, median_bbox=(%d,%d,%d,%d)",
                    tr.person_id, len(tr.frames), tr.first_seen, tr.last_seen,
                    mb.cx, mb.x, mb.y, mb.w, mb.h)
    return tracks


def tracks_to_dict(tracks: list[PersonTrack]) -> list[dict]:
    return [
        {
            "person_id":   t.person_id,
            "first_seen":  t.first_seen,
            "last_seen":   t.last_seen,
            "frame_count": len(t.frames),
            "median_bbox": {
                "x": t.median_bbox().x, "y": t.median_bbox().y,
                "w": t.median_bbox().w, "h": t.median_bbox().h,
            },
            "timeline": [
                {"t": f.t, "x": f.x, "y": f.y, "w": f.w, "h": f.h, "conf": f.conf}
                for f in t.frames
            ],
        }
        for t in tracks
    ]
