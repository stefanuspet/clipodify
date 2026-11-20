"""Router: analisis video — deteksi ekspresi wajah + jumlah orang."""

from __future__ import annotations

import logging
import os

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel
from db.database import _connect

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/analyzer", tags=["analyzer"])

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))

# Cache hasil analisis (video_path → result) agar tidak re-analyze tiap request
_analysis_cache: dict[str, dict] = {}


class AnalyzeRequest(BaseModel):
    job_id: int
    candidate_id: int | None = None   # None = analisis full video job


@router.post("/expressions")
def analyze_expressions(body: AnalyzeRequest, bg: BackgroundTasks):
    """Trigger analisis ekspresi wajah untuk satu job/video.

    Proses berjalan di background karena bisa memakan waktu.
    Poll GET /api/analyzer/expressions/{job_id}/result untuk hasilnya.
    """
    from db.database import get_job_by_id
    job = get_job_by_id(body.job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job tidak ditemukan.")

    video_path = job.get("file_path") or job.get("url", "")
    if not video_path or not os.path.exists(video_path):
        raise HTTPException(status_code=400, detail="File video tidak tersedia untuk job ini.")

    cache_key = f"{body.job_id}"
    if cache_key in _analysis_cache and _analysis_cache[cache_key].get("status") == "done":
        return {"status": "cached", "result": _analysis_cache[cache_key]["result"]}

    _analysis_cache[cache_key] = {"status": "running"}
    bg.add_task(_run_analysis, cache_key, video_path)
    return {"status": "running", "poll": f"/api/analyzer/expressions/{body.job_id}/result"}


@router.get("/expressions/{job_id}/result")
def get_analysis_result(job_id: int):
    """Ambil hasil analisis yang sudah selesai."""
    result = _analysis_cache.get(str(job_id))
    if not result:
        raise HTTPException(status_code=404, detail="Analisis belum pernah dijalankan untuk job ini.")
    return result


@router.get("/expressions/types")
def list_expression_types():
    """Daftar tipe ekspresi yang bisa dideteksi."""
    return [
        {"id": "laughing",  "label": "Laughing",  "emoji": "😂", "deepface_source": "happy"},
        {"id": "crying",    "label": "Crying",    "emoji": "😢", "deepface_source": "sad"},
        {"id": "angry",     "label": "Angry",     "emoji": "😡", "deepface_source": "angry + disgust"},
        {"id": "shocked",   "label": "Shocked",   "emoji": "😱", "deepface_source": "fear + surprise"},
        {"id": "neutral",   "label": "Neutral",   "emoji": "😐", "deepface_source": "neutral"},
    ]


class CropRequest(BaseModel):
    job_id: int
    candidate_id: int | None = None
    target_w: int = 1080
    target_h: int = 1920


@router.post("/crop")
def analyze_crop(body: CropRequest):
    """Analisis posisi wajah dan kembalikan strategi crop + FFmpeg filter string.

    Sinkron (tidak di background) karena hanya sample beberapa frame.
    """
    from db.database import get_job_by_id
    job = get_job_by_id(body.job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job tidak ditemukan.")

    video_path: str | None = None
    if body.candidate_id:
        with _connect() as conn:
            row = conn.execute(
                "SELECT output_path FROM candidates WHERE id = ?", (body.candidate_id,)
            ).fetchone()
        if row and row["output_path"] and os.path.exists(row["output_path"]):
            video_path = row["output_path"]

    if not video_path:
        fp = job.get("file_path") or ""
        if fp and os.path.exists(fp):
            video_path = fp

    if not video_path:
        raise HTTPException(status_code=400, detail="File video tidak tersedia. Download clip terlebih dahulu.")

    try:
        from backend.analyzer.crop import analyze_crop as _analyze
        result = _analyze(video_path, target_w=body.target_w, target_h=body.target_h)
        return {
            "strategy":      result.strategy,
            "people_count":  result.people_count,
            "confidence":    result.confidence,
            "video_w":       result.video_w,
            "video_h":       result.video_h,
            "ffmpeg_filter": result.ffmpeg_filter,
            "panels": [
                {
                    "person_id": p.person_id,
                    "src_x": p.src_x, "src_y": p.src_y,
                    "src_w": p.src_w, "src_h": p.src_h,
                    "out_w": p.out_w, "out_h": p.out_h,
                }
                for p in result.panels
            ],
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


class TrackCropRequest(BaseModel):
    job_id: int
    candidate_id: int | None = None
    target_w: int = 1080
    target_h: int = 1920
    detect_interval: int = 15


_track_jobs: dict[str, dict] = {}


@router.post("/track-crop")
def start_track_crop(body: TrackCropRequest, bg: BackgroundTasks):
    """Tracking crop — crop window mengikuti wajah setiap frame.

    Berjalan di background (bisa 1-5 menit tergantung panjang video).
    Poll GET /api/analyzer/track-crop/{track_id}/status.
    """
    import uuid as _uuid
    from db.database import get_job_by_id

    job = get_job_by_id(body.job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job tidak ditemukan.")

    video_path: str | None = None
    if body.candidate_id:
        with _connect() as conn:
            row = conn.execute(
                "SELECT output_path FROM candidates WHERE id = ?", (body.candidate_id,)
            ).fetchone()
        if row and row["output_path"] and os.path.exists(row["output_path"]):
            video_path = row["output_path"]

    if not video_path:
        fp = job.get("file_path") or ""
        if fp and os.path.exists(fp):
            video_path = fp

    if not video_path:
        raise HTTPException(status_code=400, detail="File video tidak tersedia. Download clip terlebih dahulu.")

    track_id = _uuid.uuid4().hex[:12]
    out_name  = f"tracked_{body.job_id}_{track_id}.mp4"
    out_path  = os.path.join(_ROOT, "tmp", out_name)
    os.makedirs(os.path.join(_ROOT, "tmp"), exist_ok=True)

    _track_jobs[track_id] = {"status": "running", "progress": 0}
    bg.add_task(_run_track_crop, track_id, video_path, out_path, body.target_w, body.target_h, body.detect_interval)
    return {
        "track_id": track_id,
        "status": "running",
        "poll": f"/api/analyzer/track-crop/{track_id}/status",
    }


@router.get("/track-crop/{track_id}/status")
def track_crop_status(track_id: str):
    info = _track_jobs.get(track_id)
    if not info:
        raise HTTPException(status_code=404, detail="Track job tidak ditemukan.")
    return info


@router.get("/track-crop/{track_id}/file")
def track_crop_file(track_id: str):
    info = _track_jobs.get(track_id)
    if not info or info.get("status") != "done":
        raise HTTPException(status_code=409, detail="Belum selesai.")
    path = info.get("output_path", "")
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="File tidak ditemukan.")
    from fastapi.responses import FileResponse
    return FileResponse(path, media_type="video/mp4", filename=os.path.basename(path))


def _run_track_crop(track_id: str, video_path: str, out_path: str, tw: int, th: int, interval: int) -> None:
    try:
        from backend.analyzer.tracker import track_and_crop

        def _progress(cur, total):
            _track_jobs[track_id]["progress"] = round(cur / total * 100) if total else 0

        result = track_and_crop(video_path, out_path, tw, th, interval, _progress)
        _track_jobs[track_id].update({
            "status":          "done",
            "output_path":     result.output_path,
            "strategy":        result.strategy,
            "people_count":    result.people_count,
            "frames_processed": result.frames_processed,
            "duration_s":      result.duration_s,
        })
    except Exception as exc:
        logger.exception("Track crop gagal untuk %s", track_id)
        _track_jobs[track_id] = {"status": "error", "error": str(exc)}


class AudioRequest(BaseModel):
    job_id: int
    window_s: float = 0.5
    hop_s:    float = 0.25


_audio_cache: dict[str, dict] = {}


@router.post("/audio")
def analyze_audio(body: AudioRequest, bg: BackgroundTasks):
    """Analisis emosi audio — energi, pitch, marah/sedih/lucu/excited.

    Berjalan di background. Poll GET /api/analyzer/audio/{job_id}/result.
    """
    from db.database import get_job_by_id
    job = get_job_by_id(body.job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job tidak ditemukan.")

    video_path = job.get("file_path") or job.get("url", "")
    if not video_path or not os.path.exists(video_path):
        raise HTTPException(status_code=400, detail="File video tidak tersedia.")

    cache_key = str(body.job_id)
    if cache_key in _audio_cache and _audio_cache[cache_key].get("status") == "done":
        return {"status": "cached", "result": _audio_cache[cache_key]["result"]}

    _audio_cache[cache_key] = {"status": "running"}
    bg.add_task(_run_audio_analysis, cache_key, video_path, body.window_s, body.hop_s)
    return {"status": "running", "poll": f"/api/analyzer/audio/{body.job_id}/result"}


@router.get("/audio/{job_id}/result")
def get_audio_result(job_id: int):
    result = _audio_cache.get(str(job_id))
    if not result:
        raise HTTPException(status_code=404, detail="Analisis audio belum dijalankan.")
    return result


@router.get("/audio/emotion-types")
def list_audio_emotion_types():
    return [
        {"id": "laughing",  "label": "Ketawa / Lucu",   "emoji": "😂", "acoustic": "energy burst + pitch variasi tinggi"},
        {"id": "angry",     "label": "Marah / Intense", "emoji": "😡", "acoustic": "energy tinggi + spectral centroid tinggi"},
        {"id": "excited",   "label": "Semangat",        "emoji": "🤩", "acoustic": "energy tinggi + pitch naik"},
        {"id": "sad",       "label": "Sedih / Pelan",   "emoji": "😢", "acoustic": "energy rendah + pitch rendah"},
        {"id": "neutral",   "label": "Normal",          "emoji": "😐", "acoustic": "semua nilai medium"},
        {"id": "silence",   "label": "Hening",          "emoji": "🔇", "acoustic": "energy sangat rendah"},
    ]


class ThumbnailRequest(BaseModel):
    job_id:       int
    candidate_id: int | None = None   # pakai clip yang sudah didownload kalau ada
    n:            int = 12


_thumb_jobs: dict[str, dict] = {}


@router.post("/thumbnail")
def request_thumbnails(body: ThumbnailRequest, bg: BackgroundTasks):
    """Ekstrak N frame kandidat thumbnail berdasarkan audio peak moments.

    Prioritas sumber video:
    1. candidate.output_path (clip sudah didownload)
    2. job.file_path (video lokal penuh)
    Untuk YouTube tanpa file lokal → 400 dengan pesan yang jelas.
    """
    from db.database import get_job_by_id, _connect

    job = get_job_by_id(body.job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job tidak ditemukan.")

    # Kandidat klip: thumbnail dari ORANG UTAMA (identitas wajah lintas shot)
    if body.candidate_id:
        cache_key = f"{body.job_id}_{body.candidate_id}"
        if cache_key in _thumb_jobs and _thumb_jobs[cache_key].get("status") == "done":
            return {"status": "cached", "result": _thumb_jobs[cache_key]["result"]}
        _thumb_jobs[cache_key] = {"status": "running"}
        out_dir = os.path.join(_ROOT, "tmp", f"thumbs_{cache_key}")
        bg.add_task(_run_person_thumbnails, cache_key, body.candidate_id, out_dir, body.n)
        return {"status": "running", "poll": f"/api/analyzer/thumbnail/{cache_key}/result"}

    video_path: str | None = None

    # 1. Coba ambil dari candidate output_path
    if body.candidate_id:
        with _connect() as conn:
            row = conn.execute(
                "SELECT output_path FROM candidates WHERE id = ?", (body.candidate_id,)
            ).fetchone()
        if row and row["output_path"] and os.path.exists(row["output_path"]):
            video_path = row["output_path"]

    # 2. Fallback ke file video penuh
    if not video_path:
        fp = job.get("file_path") or ""
        if fp and os.path.exists(fp):
            video_path = fp

    if not video_path:
        raise HTTPException(
            status_code=400,
            detail="File video tidak tersedia. Download clip terlebih dahulu via tombol Download di History.",
        )

    cache_key = f"{body.job_id}_{body.candidate_id or 'full'}"
    if cache_key in _thumb_jobs and _thumb_jobs[cache_key].get("status") == "done":
        return {"status": "cached", "result": _thumb_jobs[cache_key]["result"]}

    _thumb_jobs[cache_key] = {"status": "running"}
    audio_result = _audio_cache.get(str(body.job_id), {}).get("result")
    out_dir = os.path.join(_ROOT, "tmp", f"thumbs_{cache_key}")
    bg.add_task(_run_thumbnail_extraction, cache_key, video_path, out_dir, audio_result, body.n)
    return {"status": "running", "poll": f"/api/analyzer/thumbnail/{cache_key}/result"}


@router.get("/thumbnail/{cache_key}/result")
def get_thumbnail_result(cache_key: str):
    result = _thumb_jobs.get(cache_key)
    if not result:
        raise HTTPException(status_code=404, detail="Belum dijalankan.")
    return result


@router.get("/thumbnail/{cache_key}/frame/{filename}")
def serve_thumbnail_frame(cache_key: str, filename: str):
    """Serve JPEG frame langsung ke browser."""
    path = os.path.join(_ROOT, "tmp", f"thumbs_{cache_key}", filename)
    if not os.path.exists(path) or not filename.endswith(".jpg"):
        raise HTTPException(status_code=404, detail="Frame tidak ditemukan.")
    from fastapi.responses import FileResponse
    return FileResponse(path, media_type="image/jpeg")


def _run_person_thumbnails(cache_key: str, candidate_id: int, out_dir: str, n: int) -> None:
    try:
        from backend.editing.context import build_context
        from backend.editing.thumbnails import person_names, person_thumbnails
        ctx = build_context(candidate_id)
        result = person_thumbnails(ctx, out_dir, n=n, names=person_names(candidate_id))
        if not result:
            raise RuntimeError("Tidak ada wajah yang bisa dijadikan thumbnail di klip ini.")
        _thumb_jobs[cache_key] = {"status": "done", "result": result}
        logger.info("Thumbnail orang utama: %d frame untuk %s", len(result), cache_key)
    except Exception as exc:
        logger.exception("Thumbnail orang utama gagal untuk %s", cache_key)
        _thumb_jobs[cache_key] = {"status": "error", "error": str(exc)}


def _run_thumbnail_extraction(
    cache_key: str, video_path: str, out_dir: str,
    audio_result: dict | None, n: int,
) -> None:
    try:
        from backend.analyzer.thumbnail import extract_thumbnail_candidates
        candidates = extract_thumbnail_candidates(video_path, out_dir, audio_result, n)
        result = [
            {
                "time_s":          c.time_s,
                "filename":        os.path.basename(c.jpeg_path),
                "face_count":      c.face_count,
                "face_confidence": c.face_confidence,
                "emotion":         c.emotion,
                "energy":          c.energy,
                "score":           c.score,
            }
            for c in candidates
        ]
        _thumb_jobs[cache_key] = {"status": "done", "result": result}
        logger.info("Thumbnail extraction done: %d frames for job %s", len(result), cache_key)
    except Exception as exc:
        logger.exception("Thumbnail extraction gagal untuk %s", cache_key)
        _thumb_jobs[cache_key] = {"status": "error", "error": str(exc)}


def _run_audio_analysis(cache_key: str, video_path: str, window_s: float, hop_s: float) -> None:
    try:
        from backend.analyzer.audio import analyze_audio as _analyze
        result = _analyze(video_path, window_s=window_s, hop_s=hop_s)
        _audio_cache[cache_key] = {"status": "done", "result": result}
        logger.info("Audio analysis done for %s: %d segments", cache_key, len(result["segments"]))
    except Exception as exc:
        logger.exception("Audio analysis gagal untuk %s", cache_key)
        _audio_cache[cache_key] = {"status": "error", "error": str(exc)}


def _run_analysis(cache_key: str, video_path: str) -> None:
    try:
        from backend.analyzer.expressions import analyze_expressions
        result = analyze_expressions(video_path)
        _analysis_cache[cache_key] = {"status": "done", "result": result}
        logger.info("Expression analysis done for %s: %d segments", cache_key, len(result["segments"]))
    except Exception as exc:
        logger.exception("Expression analysis gagal untuk %s", cache_key)
        _analysis_cache[cache_key] = {"status": "error", "error": str(exc)}


# ---------------------------------------------------------------- grid clip

class GridClipRequest(BaseModel):
    job_id:       int
    candidate_id: int
    layout_style: str = "auto"   # "auto" | "speaker_focus"


_grid_jobs: dict[str, dict] = {}


@router.post("/grid-clip")
def start_grid_clip(body: GridClipRequest, bg: BackgroundTasks):
    """Buat grid clip portrait dinamis — crop tiap orang, susun berdasarkan siapa aktif/bicara.

    Poll GET /api/analyzer/grid-clip/{job_id}/status untuk hasilnya.
    """
    from db.database import get_job_by_id

    job = get_job_by_id(body.job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job tidak ditemukan.")

    video_path: str | None = None
    with _connect() as conn:
        row = conn.execute(
            "SELECT output_path FROM candidates WHERE id = ?", (body.candidate_id,)
        ).fetchone()
    if row and row["output_path"] and os.path.exists(row["output_path"]):
        video_path = row["output_path"]

    if not video_path:
        fp = job.get("file_path") or ""
        if fp and os.path.exists(fp):
            video_path = fp

    if not video_path:
        raise HTTPException(
            status_code=400,
            detail="File video tidak tersedia. Download clip terlebih dahulu.",
        )

    cache_key = f"grid_{body.candidate_id}"
    if cache_key in _grid_jobs and _grid_jobs[cache_key].get("status") == "done":
        return {"status": "cached", "result": _grid_jobs[cache_key]["result"],
                "poll": f"/api/analyzer/grid-clip/{cache_key}/status"}

    out_name = f"grid_{body.candidate_id}.mp4"
    out_path = os.path.join(_ROOT, "tmp", out_name)
    os.makedirs(os.path.join(_ROOT, "tmp"), exist_ok=True)

    _grid_jobs[cache_key] = {"status": "running", "progress": "tracking persons..."}
    audio_result = _audio_cache.get(str(body.job_id), {}).get("result")

    bg.add_task(
        _run_grid_clip,
        cache_key, video_path, out_path,
        body.layout_style,
        audio_result,
    )
    return {"status": "running", "poll": f"/api/analyzer/grid-clip/{cache_key}/status"}


@router.get("/grid-clip/{cache_key}/status")
def grid_clip_status(cache_key: str):
    info = _grid_jobs.get(cache_key)
    if not info:
        raise HTTPException(status_code=404, detail="Grid clip job tidak ditemukan.")
    return info


@router.get("/grid-clip/{cache_key}/file")
def grid_clip_file(cache_key: str):
    info = _grid_jobs.get(cache_key)
    if not info or info.get("status") != "done":
        raise HTTPException(status_code=409, detail="Belum selesai.")
    path = info.get("output_path", "")
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="File tidak ditemukan.")
    from fastapi.responses import FileResponse
    return FileResponse(path, media_type="video/mp4",
                        filename=os.path.basename(path))


def _run_grid_clip(
    cache_key: str, video_path: str, output_path: str,
    layout_style: str,
    audio_result: dict | None,
) -> None:
    import subprocess
    try:
        import cv2
        from backend.analyzer.person_tracker import track_persons, tracks_to_dict
        from backend.analyzer.speaker_detector import detect_speakers, segments_to_dict
        from backend.analyzer.activity_scorer import score_activity, activity_to_dict
        from backend.analyzer.grid_composer import plan_grid_segments, build_ffmpeg_args

        cap = cv2.VideoCapture(video_path)
        vid_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        vid_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fps   = cap.get(cv2.CAP_PROP_FPS) or 25.0
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        duration = total / fps
        cap.release()

        # Step 1: Person Tracker
        _grid_jobs[cache_key]["progress"] = "tracking persons..."
        logger.info("Grid clip [%s]: step 1 — person tracker", cache_key)
        tracks = track_persons(video_path)
        if not tracks:
            raise RuntimeError("Tidak ada orang terdeteksi dalam video.")

        # Step 2: Speaker Detector
        _grid_jobs[cache_key]["progress"] = f"detecting speakers ({len(tracks)} persons)..."
        logger.info("Grid clip [%s]: step 2 — speaker detector", cache_key)
        audio_rms = audio_result.get("segments") if audio_result else None
        speaker_segs = detect_speakers(video_path, tracks, audio_rms)

        # Step 3: Activity Scorer
        _grid_jobs[cache_key]["progress"] = "scoring activity..."
        logger.info("Grid clip [%s]: step 3 — activity scorer", cache_key)
        activity = score_activity(video_path, tracks, speaker_segs)

        # Step 4: Plan grid segments
        _grid_jobs[cache_key]["progress"] = "planning grid layout..."
        logger.info("Grid clip [%s]: step 4 — grid planner", cache_key)
        grid_segs = plan_grid_segments(tracks, activity, duration, video_path=video_path)

        # Step 5: Build FFmpeg args + render
        _grid_jobs[cache_key]["progress"] = "rendering grid video..."
        logger.info("Grid clip [%s]: step 5 — FFmpeg render", cache_key)
        script_path = os.path.join(_ROOT, "tmp", f"fc_{cache_key}.txt")
        ffmpeg_args = build_ffmpeg_args(
            video_path, grid_segs, output_path, vid_w, vid_h, script_path,
            source_fps=fps,
        )

        logger.info("FFmpeg args: %s", " ".join(ffmpeg_args[:8]) + " ...")
        result = subprocess.run(ffmpeg_args, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"FFmpeg gagal: {result.stderr[-600:]}")

        result_summary = {
            "output_path": output_path,
            "num_persons": len(tracks),
            "num_segments": len(grid_segs),
            "duration_s": round(duration, 1),
        }
        _grid_jobs[cache_key].update({
            "status": "done",
            "result": result_summary,
            "output_path": output_path,
            "persons": tracks_to_dict(tracks),
            "speaker_segments": segments_to_dict(speaker_segs)[:20],
        })
        logger.info("Grid clip [%s] selesai: %s", cache_key, output_path)

    except Exception as exc:
        logger.exception("Grid clip gagal untuk %s", cache_key)
        _grid_jobs[cache_key] = {"status": "error", "error": str(exc)}
