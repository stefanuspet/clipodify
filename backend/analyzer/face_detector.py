"""Face Detector — deteksi wajah dengan InsightFace RetinaFace.

Dipakai setelah YOLO body detection untuk dua tujuan:
1. Validasi: reject deteksi badan yang tidak punya wajah (kameraman, penonton, dll)
2. Lokasi: dapatkan koordinat wajah untuk crop yang centered di muka, bukan di badan

Mendukung non-frontal face (3/4 view, profil) yang sering muncul di podcast.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np

logger = logging.getLogger(__name__)

_MODEL_NAME = "buffalo_sc"   # model ringan ~12MB, download otomatis pertama kali
_DET_SIZE   = (320, 320)     # resolusi input deteksi; lebih kecil = lebih cepat


@dataclass
class FaceBox:
    x: int
    y: int
    w: int
    h: int
    conf: float = 1.0

    @property
    def cx(self) -> int:
        return self.x + self.w // 2

    @property
    def cy(self) -> int:
        return self.y + self.h // 2


_app = None


def _get_app():
    global _app
    if _app is None:
        from insightface.app import FaceAnalysis
        _app = FaceAnalysis(
            name=_MODEL_NAME,
            allowed_modules=["detection"],
            providers=["CPUExecutionProvider"],
        )
        _app.prepare(ctx_id=-1, det_size=_DET_SIZE)
        logger.info("InsightFace FaceAnalysis siap (model=%s)", _MODEL_NAME)
    return _app


_rec = None


def _get_rec():
    """Model pengenal wajah (ArcFace w600k_mbf) dari paket buffalo_sc yang sama."""
    global _rec
    if _rec is None:
        import os
        from insightface.model_zoo import get_model
        _get_app()  # memastikan paket model sudah terunduh
        path = os.path.expanduser(f"~/.insightface/models/{_MODEL_NAME}/w600k_mbf.onnx")
        _rec = get_model(path, providers=["CPUExecutionProvider"])
        _rec.prepare(ctx_id=-1)
    return _rec


def detect_faces(img: np.ndarray) -> list:
    """Semua wajah di gambar (objek insightface: bbox, kps, det_score)."""
    try:
        return _get_app().get(img)
    except Exception as e:
        logger.debug("InsightFace gagal: %s", e)
        return []


def face_embedding(frame: np.ndarray, fb: FaceBox) -> np.ndarray | None:
    """Embedding identitas (512-d, ternormalisasi) untuk wajah di sekitar kotak `fb`.

    Wajah dideteksi ulang di potongan sekitar kotak supaya dapat 5 titik wajah,
    lalu disejajarkan sebelum dikenali.
    """
    h, w = frame.shape[:2]
    side = int(max(fb.w, fb.h) * 2.0)
    x1, y1 = max(0, fb.cx - side // 2), max(0, fb.cy - side // 2)
    crop = frame[y1:min(h, y1 + side), x1:min(w, x1 + side)]
    if crop.size == 0:
        return None
    faces = detect_faces(crop)
    if not faces:
        return None
    ccx, ccy = crop.shape[1] / 2, crop.shape[0] / 2
    face = min(faces, key=lambda f: abs((f.bbox[0] + f.bbox[2]) / 2 - ccx) + abs((f.bbox[1] + f.bbox[3]) / 2 - ccy))
    try:
        emb = _get_rec().get(crop, face)
    except Exception as e:
        logger.debug("Embedding gagal: %s", e)
        return None
    emb = np.asarray(emb, dtype=np.float32)
    return emb / (np.linalg.norm(emb) + 1e-9)


def find_face_in_body(
    frame: np.ndarray,
    bx: int, by: int, bw: int, bh: int,
    min_conf: float = 0.55,
) -> FaceBox | None:
    """Cari wajah di upper 55% region body bbox.

    Return FaceBox dalam koordinat FRAME (bukan koordinat ROI).
    Return None kalau tidak ada wajah ditemukan (body tanpa muka = skip).
    """
    # Fokus upper 55% — wajah selalu di sana untuk pose duduk/berdiri
    roi_y2 = by + int(bh * 0.55)
    roi = frame[by:roi_y2, bx:bx + bw]
    if roi.size == 0:
        return None

    try:
        app = _get_app()
        faces = app.get(roi)
    except Exception as e:
        logger.debug("InsightFace gagal: %s", e)
        return None

    if not faces:
        return None

    # Ambil wajah dengan confidence tertinggi
    best = max(faces, key=lambda f: float(f.det_score))
    if float(best.det_score) < min_conf:
        return None

    x1, y1, x2, y2 = map(int, best.bbox)
    return FaceBox(
        x=max(0, bx + x1),
        y=max(0, by + y1),
        w=max(1, x2 - x1),
        h=max(1, y2 - y1),
        conf=float(best.det_score),
    )
