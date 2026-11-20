"""Grid Composer — generate FFmpeg filter_complex untuk portrait grid dinamis.

Layout yang didukung (semua portrait 1080×1920):
  1 orang  → single      1080×1920 (crop + scale ke satu orang)
  2 orang  → v2          atas/bawah, masing-masing 1080×960
  3 orang  → top1_bot2   1 besar atas (1080×1280) + 2 kecil bawah (540×640)
  4 orang  → top1_bot3   1 besar atas (1080×1152) + 3 kecil bawah (360×768)
  5 orang  → top2_bot3   2 atas (540×1152) + 3 bawah (360×768)
  6 orang  → grid2x3     6 sel 540×640
"""

from __future__ import annotations

import logging
import os
import subprocess
from dataclasses import dataclass
from typing import TYPE_CHECKING

import cv2
import numpy as np

from backend.analyzer.person_tracker import PersonTrack, BBox
from backend.analyzer.activity_scorer import ActivityPoint
from backend.analyzer.face_detector import find_face_in_body, FaceBox

if TYPE_CHECKING:
    from ultralytics import YOLO

logger = logging.getLogger(__name__)

OUT_W = 1080
OUT_H = 1920

_CUT_DIFF_THRESH  = 25.0   # mean abs diff (0–255) thumbnail grayscale; cut asli ~60–80, normal <15
_SAMPLE_EVERY_S   = 1.5    # jarak sampling deteksi di dalam satu shot
_MAX_SAMPLES      = 5      # maksimal sampel deteksi per shot


@dataclass
class GridSegment:
    t_start: float
    t_end: float
    layout: str                          # "single", "v2", "top1_bot2", "grid2x2", "grid2x3"
    persons: list[str]                   # person_id diurutkan: [speaker, top_reactors...]
    bboxes: dict[str, BBox]              # person_id → body bbox di segmen ini
    face_bboxes: dict[str, FaceBox] = None  # person_id → face bbox (opsional, untuk centering)
    x_bounds: dict[str, tuple[int, int]] = None  # person_id → (x_lo, x_hi) ruang crop horizontal
    speak_scores: dict[str, float] = None        # person_id → skor gerak bibir di shot ini


def _iou_bb(a: BBox, b: BBox) -> float:
    ix1 = max(a.x, b.x); iy1 = max(a.y, b.y)
    ix2 = min(a.x + a.w, b.x + b.w); iy2 = min(a.y + a.h, b.y + b.h)
    inter = max(0, ix2 - ix1) * max(0, iy2 - iy1)
    if inter == 0:
        return 0.0
    return inter / (a.area + b.area - inter)


def _nms(bboxes: list[BBox], iou_thresh: float = 0.45) -> list[BBox]:
    """NMS: pertahankan bbox terbesar, buang yang overlap tinggi."""
    if len(bboxes) <= 1:
        return bboxes
    sorted_bb = sorted(bboxes, key=lambda b: b.area, reverse=True)
    keep: list[BBox] = []
    suppressed: set[int] = set()
    for i, bb in enumerate(sorted_bb):
        if i in suppressed:
            continue
        keep.append(bb)
        for j in range(i + 1, len(sorted_bb)):
            if j not in suppressed and _iou_bb(bb, sorted_bb[j]) > iou_thresh:
                suppressed.add(j)
    return keep


def detect_shot_cuts(video_path: str) -> tuple[list[int], int, float]:
    """Scan seluruh frame, return (frame index awal tiap shot baru, total frame, fps).

    Cut = frame yang beda jauh dari frame sebelumnya. Index yang dikembalikan
    adalah frame PERTAMA dari shot baru, jadi bisa langsung dipakai sebagai batas trim.
    """
    cap = cv2.VideoCapture(video_path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    cuts: list[int] = []
    prev = None
    i = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        g = cv2.resize(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), (96, 54)).astype(np.float32)
        if prev is not None and float(np.abs(g - prev).mean()) > _CUT_DIFF_THRESH:
            cuts.append(i)
        prev = g
        i += 1
    cap.release()
    return cuts, i, fps


def _detect_persons_at(
    cap: "cv2.VideoCapture",
    fps: float,
    yolo_model: "YOLO",
    t: float,
    vid_w: int,
    vid_h: int,
    max_persons: int = 6,
    conf: float = 0.40,
) -> tuple[list[BBox], dict[int, FaceBox]]:
    """Sample satu frame pada waktu t, detect persons (YOLO + NMS) + wajah (InsightFace).

    Return (body_bboxes, face_map) di mana face_map[i] adalah face bbox untuk body ke-i.
    Body tanpa wajah TIDAK dibuang di sini — keputusan di pemanggil (ada konteks track).
    """
    cap.set(cv2.CAP_PROP_POS_FRAMES, int(t * fps))
    ret, frame = cap.read()
    if not ret:
        return [], {}

    results = yolo_model(frame, classes=[0], conf=conf, verbose=False)
    bboxes: list[BBox] = []
    for r in results:
        for box in r.boxes:
            x1, y1, x2, y2 = map(int, box.xyxy[0])
            bb = BBox(
                x=max(0, x1), y=max(0, y1),
                w=min(x2 - x1, vid_w - x1),
                h=min(y2 - y1, vid_h - y1),
                t=t, conf=float(box.conf[0]),
            )
            if bb.w > 30 and bb.h > 60:
                bboxes.append(bb)

    bboxes = _nms(bboxes)
    if bboxes:
        max_area = max(b.area for b in bboxes)
        bboxes = [b for b in bboxes if b.area >= max_area * 0.15]
    bboxes = sorted(bboxes, key=lambda b: b.area, reverse=True)[:max_persons]
    bboxes.sort(key=lambda b: b.cx)

    # --- InsightFace: cari wajah di tiap body region ---
    face_map: dict[int, FaceBox] = {}
    for i, bb in enumerate(bboxes):
        fb = find_face_in_body(frame, bb.x, bb.y, bb.w, bb.h)
        if fb is not None:
            face_map[i] = fb

    return bboxes, face_map


def _detect_shot_persons(
    cap: "cv2.VideoCapture",
    fps: float,
    yolo_model: "YOLO",
    sample_ts: list[float],
    vid_w: int,
    vid_h: int,
    max_persons: int,
) -> tuple[list[BBox], list[FaceBox | None]]:
    """Deteksi orang di beberapa titik dalam satu shot, gabungkan jadi satu hasil stabil.

    Jumlah orang = jumlah yang paling sering muncul. Posisi tiap orang = median
    dari sampel yang jumlahnya cocok (urut kiri→kanan), jadi jitter YOLO hilang.
    """
    samples: list[tuple[list[BBox], dict[int, FaceBox]]] = []
    for ts in sample_ts:
        bbs, faces = _detect_persons_at(cap, fps, yolo_model, ts, vid_w, vid_h, max_persons=max_persons)
        # Body tanpa wajah dibuang kalau ada body lain yang punya wajah
        if 0 < len(faces) < len(bbs):
            keep = sorted(faces.keys())
            bbs = [bbs[i] for i in keep]
            faces = {j: faces[i] for j, i in enumerate(keep)}
        if bbs:
            samples.append((bbs, faces))

    if not samples:
        return [], []

    counts = [len(b) for b, _ in samples]
    n = max(set(counts), key=lambda c: (counts.count(c), c))
    matching = [s for s in samples if len(s[0]) == n]

    bboxes: list[BBox] = []
    faces_out: list[FaceBox | None] = []
    for k in range(n):
        bboxes.append(_median_bbox([b[k] for b, _ in matching]))
        fbs = [f[k] for _, f in matching if k in f]
        if fbs:
            faces_out.append(FaceBox(
                x=int(np.median([f.x for f in fbs])), y=int(np.median([f.y for f in fbs])),
                w=int(np.median([f.w for f in fbs])), h=int(np.median([f.h for f in fbs])),
                conf=float(np.median([f.conf for f in fbs])),
            ))
        else:
            faces_out.append(None)
    return bboxes, faces_out


_MOUTH_SAMPLE_FPS = 8      # sampling gerak bibir di dalam shot
_MOUTH_MAX_SAMPLES = 80    # batas sampel per shot (shot panjang → step diperbesar)


def _make_mouth_landmarker():
    """FaceLandmarker 1-wajah untuk dijalankan di potongan wajah yang sudah diperbesar.

    Di frame penuh wajah podcast terlalu kecil untuk MediaPipe (0 wajah terdeteksi
    di wide shot), tapi di potongan 256px dari kotak InsightFace hampir selalu ketemu.
    """
    try:
        import mediapipe as mp
        from backend.analyzer.speaker_detector import _ensure_model
        return mp.tasks.vision.FaceLandmarker.create_from_options(
            mp.tasks.vision.FaceLandmarkerOptions(
                base_options=mp.tasks.BaseOptions(model_asset_path=_ensure_model()),
                num_faces=1,
                min_face_detection_confidence=0.3,
                min_face_presence_confidence=0.3,
            ))
    except Exception as e:
        logger.warning("Mouth landmarker tidak tersedia: %s", e)
        return None


def _mouth_scores(
    cap: "cv2.VideoCapture",
    fps: float,
    f_start: int,
    f_end: int,
    faces: dict[str, FaceBox],
    landmarker,
) -> dict[str, float]:
    """Skor bicara per orang = rata-rata perubahan bukaan bibir antar sampel.

    Bukaan bibir dinormalisasi tinggi wajah. Pakai perubahan (bukan bukaan rata-rata)
    supaya orang yang tertawa / mulut terbuka diam tidak dianggap bicara.
    """
    import mediapipe as mp

    n_frames = f_end - f_start
    step = max(1, round(fps / _MOUTH_SAMPLE_FPS), n_frames // _MOUTH_MAX_SAMPLES)
    series: dict[str, list[float]] = {pid: [] for pid in faces}
    cap.set(cv2.CAP_PROP_POS_FRAMES, f_start)
    for fi in range(f_start, f_end):
        ok, frame = cap.read()
        if not ok:
            break
        if (fi - f_start) % step:
            continue
        for pid, fb in faces.items():
            side = int(max(fb.w, fb.h) * 1.8)
            x1 = max(0, fb.cx - side // 2)
            y1 = max(0, fb.cy - side // 2)
            roi = frame[y1:y1 + side, x1:x1 + side]
            if roi.size == 0:
                continue
            roi = cv2.cvtColor(cv2.resize(roi, (256, 256)), cv2.COLOR_BGR2RGB)
            res = landmarker.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=roi))
            if not res.face_landmarks:
                continue
            lm = res.face_landmarks[0]
            ys = [p.y for p in lm]
            face_h = max(max(ys) - min(ys), 1e-3)
            series[pid].append(abs(lm[13].y - lm[14].y) / face_h)

    return {
        pid: float(np.mean(np.abs(np.diff(v)))) if len(v) >= 4 else 0.0
        for pid, v in series.items()
    }


def plan_grid_segments(
    tracks: list[PersonTrack],
    activity: dict[str, list[ActivityPoint]],
    duration: float,
    video_path: str = "",
) -> list[GridSegment]:
    """Satu segmen per shot kamera.

    Batas segmen = frame cut kamera yang sebenarnya, jadi layout dan crop selalu
    berganti tepat bersamaan dengan gambar. Di dalam satu shot, layout dan crop tetap.
    """
    from pathlib import Path as _Path

    tracks_sorted_cx = sorted(tracks, key=lambda tr: tr.median_bbox().cx)
    max_p = 6

    yolo_model = None
    cap: "cv2.VideoCapture | None" = None
    fps_val = 25.0
    vid_w_val, vid_h_val = 1920, 1080
    cut_frames: list[int] = []
    total_frames = 0

    if video_path and _Path(video_path).exists():
        cut_frames, total_frames, fps_val = detect_shot_cuts(video_path)
        try:
            from ultralytics import YOLO as _YOLO
            from backend.analyzer.person_tracker import _MODEL_PATH
            yolo_model = _YOLO(str(_MODEL_PATH))
            cap = cv2.VideoCapture(video_path)
            vid_w_val = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            vid_h_val = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        except Exception as e:
            logger.warning("YOLO tidak tersedia untuk per-shot detection: %s", e)
            yolo_model = None

    if total_frames <= 0:
        total_frames = max(1, int(round(duration * fps_val)))

    mouth_lm = None
    mouth_lm_tried = False

    bounds = [0] + [c for c in cut_frames if 0 < c < total_frames] + [total_frames]
    logger.info("Shot detection: %d cut → %d shot", len(bounds) - 2, len(bounds) - 1)

    segments: list[GridSegment] = []
    for f_start, f_end in zip(bounds[:-1], bounds[1:]):
        t_start = f_start / fps_val
        t_end = f_end / fps_val
        shot_len = t_end - t_start

        # Titik sampel: merata di dalam shot, hindari frame paling pinggir
        n_samp = max(1, min(_MAX_SAMPLES, int(shot_len / _SAMPLE_EVERY_S) + 1))
        sample_ts = [t_start + shot_len * (i + 1) / (n_samp + 1) for i in range(n_samp)]

        bboxes: list[BBox] = []
        faces: list[FaceBox | None] = []
        if yolo_model is not None and cap is not None:
            bboxes, faces = _detect_shot_persons(
                cap, fps_val, yolo_model, sample_ts, vid_w_val, vid_h_val, max_p,
            )

        if not bboxes:
            for tr in tracks_sorted_cx:
                nearby = [f for f in tr.frames if t_start <= f.t < t_end]
                if nearby:
                    bboxes.append(_median_bbox(nearby))
                    faces.append(None)
            bboxes_faces = sorted(zip(bboxes, faces), key=lambda bf: bf[0].cx)
            bboxes = [b for b, _ in bboxes_faces]
            faces = [f for _, f in bboxes_faces]

        if not bboxes:
            # Shot tanpa orang: tetap isi agar timeline tidak bolong (crop tengah)
            segments.append(GridSegment(t_start=t_start, t_end=t_end, layout="single",
                                        persons=["_none"], bboxes={}, face_bboxes=None))
            continue

        # Jumlah orang = yang benar-benar terlihat di shot ini (bukan jumlah track global)
        n = min(len(bboxes), 6)
        layout = _choose_layout(n)

        # Track global hanya untuk identitas speaker: pasangkan by jarak terdekat,
        # sisanya dapat id lokal (tanpa info speaker).
        pairs = sorted(
            (abs(tr.median_bbox().cx - bboxes[i].cx), i, tr.person_id)
            for i in range(n) for tr in tracks_sorted_cx
        )
        idx_to_pid: dict[int, str] = {}
        used: set[str] = set()
        for dist, i, tpid in pairs:
            if i in idx_to_pid or tpid in used or dist > vid_w_val * 0.12:
                continue
            idx_to_pid[i] = tpid
            used.add(tpid)

        pid_list: list[str] = []
        bbox_dict: dict[str, BBox] = {}
        face_dict: dict[str, FaceBox] = {}
        for i in range(n):
            pid = idx_to_pid.get(i, f"P{i}")
            bbox_dict[pid] = bboxes[i]
            if faces[i] is not None:
                face_dict[pid] = faces[i]
            pid_list.append(pid)
        x_bounds = _compute_x_bounds(
            [(pid_list[i], bboxes[i], faces[i]) for i in range(n)], vid_w_val,
        )

        # Cell utama: orang yang paling banyak bicara di shot ini (gerak bibir),
        # di antara yang wajahnya utuh di frame.
        mouth: dict[str, float] = {}
        if n >= 2 and len(face_dict) >= 2 and cap is not None:
            if not mouth_lm_tried:
                mouth_lm = _make_mouth_landmarker()
                mouth_lm_tried = True
            if mouth_lm is not None:
                mouth = _mouth_scores(cap, fps_val, f_start, f_end, face_dict, mouth_lm)

        def _speak_count(pid: str) -> float:
            if any(mouth.values()):
                return mouth.get(pid, 0.0)
            return sum(1 for p in activity.get(pid, []) if t_start <= p.t < t_end and p.is_speaker)

        def _face_cut(pid: str) -> bool:
            f = face_dict.get(pid)
            if f is None:
                return False
            return f.x <= 2 or f.x + f.w >= vid_w_val - 2 or f.w < f.h * 0.6

        candidates = [p for p in pid_list if not _face_cut(p)] or pid_list
        primary = max(candidates, key=_speak_count)
        pid_list.remove(primary)
        pid_list.insert(0, primary)

        logger.info("Shot f=%d–%d (t=%.2f–%.2f) layout=%s persons=%s faces=%d/%d samples=%d mouth=%s",
                    f_start, f_end, t_start, t_end, layout, pid_list, len(face_dict), n, n_samp,
                    {k: round(v, 4) for k, v in mouth.items()})
        segments.append(GridSegment(
            t_start=t_start, t_end=t_end, layout=layout, persons=pid_list,
            bboxes=bbox_dict, face_bboxes=face_dict or None, x_bounds=x_bounds,
            speak_scores=mouth or None,
        ))

    if cap is not None:
        cap.release()
    if mouth_lm is not None:
        mouth_lm.close()
    return segments


def build_ffmpeg_args(
    video_path: str,
    segments: list[GridSegment],
    output_path: str,
    vid_w: int,
    vid_h: int,
    script_path: str,
    source_fps: float = 25.0,
) -> list[str]:
    """Bangun argumen FFmpeg sebagai list (bukan string shell) untuk menghindari
    quoting issues dengan nama file emoji/spasi dan filter_complex panjang.

    Filter_complex ditulis ke `script_path` dan dibaca via -filter_complex_script.
    """
    fc, use_script, single = _build_filter(segments, vid_w, vid_h, source_fps)

    if use_script:
        # Tulis filter ke file untuk menghindari arg length limit
        with open(script_path, "w", encoding="utf-8") as f:
            f.write(fc)
        args = [
            "ffmpeg", "-y", "-i", video_path,
            "-filter_complex_script", script_path,
            "-map", "[out]", "-map", "[aout]",
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-c:a", "aac", "-b:a", "128k",
            output_path,
        ]
    elif single:
        args = [
            "ffmpeg", "-y", "-i", video_path,
            "-vf", fc,
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-c:a", "aac", "-b:a", "128k",
            output_path,
        ]
    else:
        with open(script_path, "w", encoding="utf-8") as f:
            f.write(fc)
        args = [
            "ffmpeg", "-y", "-i", video_path,
            "-filter_complex_script", script_path,
            "-map", "[out]", "-map", "0:a?",
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-c:a", "aac", "-b:a", "128k",
            output_path,
        ]
    return args


# Keep old name as alias for backward compat
def build_filter_complex(
    video_path: str,
    segments: list[GridSegment],
    output_path: str,
    vid_w: int,
    vid_h: int,
) -> str:
    """Deprecated — pakai build_ffmpeg_args. Kept for compat."""
    raise NotImplementedError("Pakai build_ffmpeg_args")


def _build_filter(
    segments: list[GridSegment],
    vid_w: int,
    vid_h: int,
    source_fps: float = 25.0,
    always_trim: bool = False,
    audio_fade_s: float = 0.0,
) -> tuple[str, bool, bool]:
    """Return (filter_string, use_script, is_single_vf).

    use_script=True  → multi-segmen, tulis ke file, pakai -filter_complex_script
    is_single_vf=True → 1 segmen, 1 orang, bisa pakai -vf
    """
    return _build_filter_impl(segments, vid_w, vid_h, source_fps, always_trim, audio_fade_s)


def _build_filter_impl(
    segments: list[GridSegment],
    vid_w: int,
    vid_h: int,
    source_fps: float = 25.0,
    always_trim: bool = False,
    audio_fade_s: float = 0.0,
) -> tuple[str, bool, bool]:
    """Return (filter_string, use_script, is_single_vf).

    use_script=True  → pakai -filter_complex_script (multi-segmen atau panjang)
    is_single_vf=True → 1 orang, boleh pakai -vf sederhana
    """
    # Segmen tidak di-merge / dibuang: tiap segmen = satu shot, timeline harus utuh
    merged = segments
    logger.info("Grid composer: %d segmen (shot), video=%dx%d", len(merged), vid_w, vid_h)
    # always_trim: segmen adalah potongan timeline bebas (editor), boleh tanpa orang (center crop)
    if not always_trim and not any(s.bboxes for s in merged):
        raise ValueError("Tidak ada segmen valid — tidak ada orang yang terdeteksi")

    def _get_fb(seg: GridSegment, pid: str) -> "FaceBox | None":
        """Helper: ambil face bbox kalau tersedia."""
        return (seg.face_bboxes or {}).get(pid)

    if len(merged) == 1 and not always_trim:
        seg = merged[0]
        n = len(seg.persons)
        if n == 1:
            pid = seg.persons[0]
            bb = seg.bboxes.get(pid)
            fb = _get_fb(seg, pid)
            fc = _crop_scale_expr(bb, vid_w, vid_h, OUT_W, OUT_H, fb)
            return fc, False, True
        # Multi-orang, 1 segmen
        fc_parts: list[str] = []
        fc_parts.append(f"[0:v]split={n}" + "".join(f"[sv0p{j}]" for j in range(n)))
        cell_parts = []
        xb = seg.x_bounds or {}
        for j, pid in enumerate(seg.persons):
            bb = seg.bboxes.get(pid)
            fb = _get_fb(seg, pid)
            cw, ch = _cell_size(seg.layout, j, n)
            lo, hi = xb.get(pid, (0, vid_w))
            fc_parts.append(f"[sv0p{j}]{_crop_scale_expr(bb, vid_w, vid_h, cw, ch, fb, lo, hi)}[cp0_{j}]")
            cell_parts.append(f"[cp0_{j}]")
        stack_f = _stack_filter(cell_parts, seg.layout, n, 0)
        fc_parts.append(stack_f + "[out]")
        fc_parts.append("[0:a]acopy[aout]")
        return ";".join(fc_parts), True, False

    # Multi-segmen: trim + per-segmen grid + concat
    fc_parts = []

    for si, seg in enumerate(merged):
        # Trim pakai nomor frame agar batas tepat di frame cut (tanpa pembulatan waktu)
        f_s = int(round(seg.t_start * source_fps))
        f_e = int(round(seg.t_end * source_fps))
        a_s = f_s / source_fps
        a_e = f_e / source_fps
        fc_parts.append(f"[0:v]trim=start_frame={f_s}:end_frame={f_e},setpts=PTS-STARTPTS,format=yuv420p[sv{si}]")
        afade = ""
        if audio_fade_s > 0 and a_e - a_s > 4 * audio_fade_s:
            # fade sangat pendek di tiap sambungan supaya jump cut tidak bunyi "klik"
            afade = (f",afade=t=in:d={audio_fade_s:.3f}"
                     f",afade=t=out:st={a_e - a_s - audio_fade_s:.4f}:d={audio_fade_s:.3f}")
        fc_parts.append(f"[0:a]atrim=start={a_s:.6f}:end={a_e:.6f},asetpts=PTS-STARTPTS{afade}[sa{si}]")

        n = len(seg.persons)
        if n == 1:
            pid = seg.persons[0]
            bb = seg.bboxes.get(pid)
            fb = _get_fb(seg, pid)
            # Tambah scale exact di akhir untuk jamin dimensi 1080×1920 sebelum concat
            fc_parts.append(
                f"[sv{si}]{_crop_scale_expr(bb, vid_w, vid_h, OUT_W, OUT_H, fb)},"
                f"scale={OUT_W}:{OUT_H},setsar=1[vout{si}]"
            )
        else:
            fc_parts.append(f"[sv{si}]split={n}" +
                            "".join(f"[sv{si}p{j}]" for j in range(n)))
            cell_parts = []
            xb = seg.x_bounds or {}
            for j, pid in enumerate(seg.persons):
                bb = seg.bboxes.get(pid)
                fb = _get_fb(seg, pid)
                cw, ch = _cell_size(seg.layout, j, n)
                lo, hi = xb.get(pid, (0, vid_w))
                fc_parts.append(f"[sv{si}p{j}]{_crop_scale_expr(bb, vid_w, vid_h, cw, ch, fb, lo, hi)}[cp{si}_{j}]")
                cell_parts.append(f"[cp{si}_{j}]")
            stack_f = _stack_filter(cell_parts, seg.layout, n, si)
            fc_parts.append(stack_f + f"[vout{si}]")

    # Hard cut concat — koordinat dan grid berubah tepat di boundary yang sama
    n_seg = len(merged)
    interleaved = "".join(f"[vout{i}][sa{i}]" for i in range(n_seg))
    fc_parts.append(f"{interleaved}concat=n={n_seg}:v=1:a=1[out][aout]")
    return ";".join(fc_parts), True, False


_TOP1_BOT3_H_TOP = 1152   # top cell height for top1_bot3 / top2_bot3
_TOP1_BOT3_H_BOT = OUT_H - _TOP1_BOT3_H_TOP   # 768


def _cell_size(layout: str, idx: int, n: int) -> tuple[int, int]:
    """Ukuran cell untuk orang ke-idx dalam layout."""
    if layout == "single":    return OUT_W, OUT_H
    if layout == "v2":        return OUT_W, OUT_H // 2
    if layout == "top1_bot2":
        return (OUT_W, 1280) if idx == 0 else (OUT_W // 2, OUT_H - 1280)
    if layout == "top1_bot3":
        # 1 besar atas (1080×1152) + 3 kecil bawah (360×768)
        return (OUT_W, _TOP1_BOT3_H_TOP) if idx == 0 else (OUT_W // 3, _TOP1_BOT3_H_BOT)
    if layout == "top2_bot3":
        # 2 atas (540×1152) + 3 bawah (360×768)
        return (OUT_W // 2, _TOP1_BOT3_H_TOP) if idx < 2 else (OUT_W // 3, _TOP1_BOT3_H_BOT)
    if layout == "grid2x2":   return OUT_W // 2, OUT_H // 2
    if layout == "grid2x3":   return OUT_W // 2, OUT_H // 3
    return OUT_W, OUT_H


def _crop_rect(
    bb: BBox,
    vid_w: int, vid_h: int,
    out_w: int, out_h: int,
    fb: "FaceBox | None" = None,
    x_lo: int = 0,
    x_hi: int | None = None,
) -> tuple[int, int, int, int, "FaceBox | None"]:
    """Kotak crop (w, h, rx, ry) di frame sumber + wajah yang benar-benar dipakai.

    Crop tidak pernah keluar dari [x_lo, x_hi] (titik tengah ke orang sebelah),
    supaya satu orang tidak muncul di dua cell. Kalau ruangnya sempit, crop
    diperkecil (lebih zoom) dengan aspect ratio tetap, bukan digeser ke tetangga.
    """
    target_ar = out_w / out_h
    lo = max(0, x_lo)
    hi = vid_w if x_hi is None else min(vid_w, x_hi)

    if fb is not None:
        # Wajah di ~20% dari atas crop; tinggi crop ≈ 4.5× wajah (min 25% frame)
        h = max(int(fb.h * 4.5), int(vid_h * 0.25))
        w = int(h * target_ar)
        cx = fb.cx
        top = fb.cy - int(h * 0.20) - fb.h // 2
        # Wajah palsu di background: pusat badan tidak masuk crop → pakai badan
        if not (top <= bb.cy <= top + h):
            fb = None

    if fb is None:
        # Upper-body heuristic
        bbox_ar = bb.w / max(bb.h, 1)
        use_h = int(bb.h * 0.60) if bbox_ar < target_ar else bb.h
        pad_x   = int(bb.w * 0.08)
        pad_top = int(use_h * 0.20)
        pad_bot = int(use_h * 0.05)
        w0 = bb.w + 2 * pad_x
        h0 = use_h + pad_top + pad_bot
        if w0 / max(h0, 1) < target_ar:
            w, h = int(h0 * target_ar), h0
        else:
            w, h = w0, int(w0 / target_ar)
        cx = bb.cx
        top = bb.y - pad_top

    # Pas-kan ke ruang horizontal yang boleh dipakai (tidak melewati tetangga)
    if w > hi - lo:
        w = hi - lo
        h = int(w / target_ar)
        if fb is not None:
            top = fb.cy - int(h * 0.20) - fb.h // 2
    if h > vid_h:
        h = vid_h
        w = min(w, int(h * target_ar))
    w = max(w, 16); h = max(h, 16)

    # Geser hanya di dalam [lo, hi]; orang di tepi frame boleh tidak di tengah
    rx = min(max(cx - w // 2, lo), max(lo, hi - w))
    ry = min(max(top, 0), vid_h - h)
    return w, h, rx, ry, fb


def _crop_scale_expr(
    bb: BBox | None,
    vid_w: int, vid_h: int,
    out_w: int, out_h: int,
    fb: "FaceBox | None" = None,
    x_lo: int = 0,
    x_hi: int | None = None,
) -> str:
    """Crop region orang → scale ke output cell (lihat _crop_rect)."""
    tail = (f"scale={out_w}:{out_h}:force_original_aspect_ratio=increase,"
            f"crop={out_w}:{out_h}:(iw-{out_w})/2:0,setsar=1")
    if bb is None:
        cw = min(vid_w, int(vid_h * out_w / out_h))
        ch = min(vid_h, int(vid_w * out_h / out_w))
        sx = (vid_w - cw) // 2; sy = (vid_h - ch) // 2
        return f"crop={cw}:{ch}:{sx}:{sy}," + tail
    w, h, rx, ry, _ = _crop_rect(bb, vid_w, vid_h, out_w, out_h, fb, x_lo, x_hi)
    return f"crop={w}:{h}:{rx}:{ry}," + tail


def _cell_rects(layout: str, n: int) -> list[tuple[int, int, int, int]]:
    """(x, y, w, h) tiap cell di kanvas output, urutan sama dengan _stack_filter."""
    sz = [_cell_size(layout, i, n) for i in range(n)]
    if layout == "v2":
        pos = [(0, 0), (0, OUT_H // 2)]
    elif layout == "top1_bot2":
        pos = [(0, 0), (0, 1280), (OUT_W // 2, 1280)]
    elif layout == "top1_bot3":
        pos = [(0, 0)] + [((OUT_W // 3) * (i - 1), _TOP1_BOT3_H_TOP) for i in range(1, 4)]
    elif layout == "top2_bot3":
        pos = [(0, 0), (OUT_W // 2, 0)] + [((OUT_W // 3) * (i - 2), _TOP1_BOT3_H_TOP) for i in range(2, 5)]
    elif layout == "grid2x2":
        pos = [(0, 0), (OUT_W // 2, 0), (0, OUT_H // 2), (OUT_W // 2, OUT_H // 2)]
    elif layout == "grid2x3":
        ch3 = OUT_H // 3
        pos = [(0, 0), (OUT_W // 2, 0), (0, ch3), (OUT_W // 2, ch3), (0, 2 * ch3), (OUT_W // 2, 2 * ch3)]
        if n == 5:
            pos[4] = (OUT_W // 4, 2 * ch3)
    else:
        pos = [(0, 0)]
    return [(x, y, w, h) for (x, y), (w, h) in zip(pos[:n], sz)]


def layout_seams(layout: str, n: int) -> list[int]:
    """Garis sambungan horizontal antar baris cell (koordinat Y output)."""
    ys = sorted({y for _, y, _, _ in _cell_rects(layout, n) if y > 0})
    return ys


def output_face_zones(seg: "GridSegment", vid_w: int, vid_h: int) -> list[tuple[int, int, int, int]]:
    """Kotak kepala tiap orang (x0, y0, x1, y1) di kanvas 1080×1920 untuk satu segmen."""
    return list(output_face_zones_by_pid(seg, vid_w, vid_h).values())


def output_face_zones_by_pid(seg: "GridSegment", vid_w: int, vid_h: int) -> dict[str, tuple[int, int, int, int]]:
    """Seperti output_face_zones, tapi per person_id lokal shot.

    Memakai crop yang sama persis dengan render. Kalau wajah tidak terdeteksi,
    kepala ditaksir dari bagian atas badan.
    """
    persons = [p for p in seg.persons if p in seg.bboxes]
    n = len(seg.persons)
    cells = _cell_rects(seg.layout, n)
    xb = seg.x_bounds or {}
    zones: dict[str, tuple[int, int, int, int]] = {}
    for j, pid in enumerate(seg.persons):
        if pid not in persons or j >= len(cells):
            continue
        bb = seg.bboxes[pid]
        fb = (seg.face_bboxes or {}).get(pid)
        cx, cy, cw, ch = cells[j]
        lo, hi = xb.get(pid, (0, vid_w)) if n > 1 else (0, vid_w)
        w, h, rx, ry, used = _crop_rect(bb, vid_w, vid_h, cw, ch, fb, lo, hi)
        if used is not None:
            fx, fy, fw, fh = used.x, used.y, used.w, used.h
        else:
            fw = int(bb.w * 0.4); fh = int(bb.h * 0.22)
            fx = bb.cx - fw // 2; fy = bb.y
        s = max(cw / w, ch / h)
        offx = (w * s - cw) / 2
        x0 = cx + (fx - rx) * s - offx
        y0 = cy + (fy - ry) * s
        x1 = x0 + fw * s
        y1 = y0 + fh * s
        z = (int(max(cx, x0)), int(max(cy, y0)), int(min(cx + cw, x1)), int(min(cy + ch, y1)))
        if z[2] > z[0] and z[3] > z[1]:
            zones[pid] = z
    return zones


def _compute_x_bounds(
    people: list[tuple[str, BBox, "FaceBox | None"]],
    vid_w: int,
) -> dict[str, tuple[int, int]]:
    """Ruang horizontal crop tiap orang, dihitung dari SEMUA orang di frame.

    Dasar: titik tengah antar wajah. Lalu dipersempit sampai tepi badan tetangga
    (supaya bahu tetangga tidak ikut), tapi tidak pernah memotong wajah sendiri.
    """
    items = []
    for pid, bb, fb in people:
        if fb is not None:
            items.append((fb.cx, fb.x, fb.x + fb.w, bb.x, bb.x2, pid))
        else:
            items.append((bb.cx, bb.cx - bb.w // 6, bb.cx + bb.w // 6, bb.x, bb.x2, pid))
    items.sort()
    out: dict[str, tuple[int, int]] = {}
    for i, (cx, fx1, fx2, _bx1, _bx2, pid) in enumerate(items):
        margin = (fx2 - fx1) // 4
        lo, hi = 0, vid_w
        if i > 0:
            l_cx, _, _, _, l_bx2, _ = items[i - 1]
            lo = max((l_cx + cx) // 2, min(l_bx2, fx1 - margin))
        if i < len(items) - 1:
            r_cx, _, _, r_bx1, _, _ = items[i + 1]
            hi = min((cx + r_cx) // 2, max(r_bx1, fx2 + margin))
        out[pid] = (lo, max(hi, lo + 16))
    return out


def _stack_filter(cell_labels: list[str], layout: str, n: int, si: int) -> str:
    """Bangun stack filter — selalu akhiri dengan scale+setsar untuk dimensi exact."""
    p = cell_labels
    # Akhiri setiap layout dengan scale exact untuk memastikan 1080×1920 di concat
    final = f",scale={OUT_W}:{OUT_H},setsar=1"
    if layout == "v2":
        return f"{p[0]}{p[1]}vstack{final}"
    if layout == "top1_bot2":
        return f"{p[1]}{p[2]}hstack[bot{si}];{p[0]}[bot{si}]vstack{final}"
    if layout == "top1_bot3":
        # p[0]=1080×1152, p[1..3]=360×768 each → hstack → 1080×768 → vstack → 1080×1920
        cw3 = OUT_W // 3
        return (f"{p[1]}{p[2]}hstack[b01_{si}];[b01_{si}]{p[3]}hstack[bot{si}];"
                f"{p[0]}[bot{si}]vstack{final}")
    if layout == "top2_bot3":
        # p[0..1]=540×1152 hstack→1080×1152, p[2..4]=360×768 hstack→1080×768, vstack→1080×1920
        cw3 = OUT_W // 3
        return (f"{p[0]}{p[1]}hstack[top{si}];"
                f"{p[2]}{p[3]}hstack[b01_{si}];[b01_{si}]{p[4]}hstack[bot{si}];"
                f"[top{si}][bot{si}]vstack{final}")
    if layout == "grid2x2":
        return f"{p[0]}{p[1]}hstack[top{si}];{p[2]}{p[3]}hstack[bot{si}];[top{si}][bot{si}]vstack{final}"
    if layout == "grid2x3":
        if n == 5:
            cw2 = OUT_W // 2; ch3 = OUT_H // 3
            return (f"{p[0]}{p[1]}hstack[r0_{si}];{p[2]}{p[3]}hstack[r1_{si}];"
                    f"{p[4]}pad={OUT_W}:{ch3}:{cw2//2}:0[r2_{si}];"
                    f"[r0_{si}][r1_{si}]vstack[top{si}];[top{si}][r2_{si}]vstack{final}")
        return (f"{p[0]}{p[1]}hstack[r0_{si}];{p[2]}{p[3]}hstack[r1_{si}];"
                f"{p[4]}{p[5]}hstack[r2_{si}];"
                f"[r0_{si}][r1_{si}]vstack[mid{si}];[mid{si}][r2_{si}]vstack{final}")
    return f"{p[0]}copy" if p else ""  # fallback




def _choose_layout(n: int) -> str:
    if n <= 1: return "single"
    if n == 2: return "v2"
    if n == 3: return "top1_bot2"
    if n == 4: return "top1_bot3"
    if n == 5: return "top2_bot3"
    return "grid2x3"


def _median_bbox(bboxes: list[BBox]) -> BBox:
    return BBox(
        x=int(np.median([b.x for b in bboxes])),
        y=int(np.median([b.y for b in bboxes])),
        w=int(np.median([b.w for b in bboxes])),
        h=int(np.median([b.h for b in bboxes])),
    )
