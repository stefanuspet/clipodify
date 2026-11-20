"""Router: pipeline jobs — process video, list, get status."""

from __future__ import annotations

import logging
import os
from concurrent.futures import ThreadPoolExecutor

from fastapi import APIRouter, BackgroundTasks, HTTPException, WebSocket, WebSocketDisconnect

from backend.schemas import JobResponse, MetadataResponse, ProcessRequest
from backend.websocket import emit_done, emit_error, emit_progress, manager

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/jobs", tags=["jobs"])

_executor = ThreadPoolExecutor(max_workers=2)


# ---------------------------------------------------------------- helpers

def _job_to_response(job: dict, candidates_count: int = 0) -> JobResponse:
    return JobResponse(
        id=job["id"],
        url=job["url"],
        video_id=job["video_id"],
        title=job["title"],
        status=job["status"],
        source_type=job.get("source_type"),
        mode=job.get("mode"),
        has_comments=job.get("has_comments"),
        campaign_product=job.get("campaign_product"),
        created_at=job.get("created_at"),
        thumbnail=job.get("thumbnail"),
        duration=job.get("duration"),
        candidates_count=candidates_count,
    )


def _run_pipeline(
    job_id: int,
    src: str,
    video_id: str,
    title: str,
    language: str | None,
    min_seconds: int,
    max_seconds: int,
    performance_hint: str | None,
    mode: str,
    campaign: dict,
) -> None:
    """Pipeline berjalan di thread pool — progress dikirim via WebSocket."""
    channel = f"job_{job_id}"
    TOTAL = 6

    try:
        # Import di sini karena sys.path diset di main.py startup
        from core import audio_analyzer, comment_analyzer, narrative_analyzer, text_analyzer
        from core.downloader import cleanup_tmp
        from core.fusion import fuse
        from core.gemini_analyzer import analyze as analyze_gemini
        from core import gemini_analyzer
        from core.transcriber import transcribe
        from core.validator import validate
        from core.windowing import build_windows
        from core import source
        from db.database import (
            get_candidates_by_job,
            save_candidates,
            save_segments,
            set_job_has_comments,
            update_job_status,
        )
        from utils.constants import WHISPER_LANGUAGE_MIN_PROB

        is_local = source.detect_type(src) == source.LOCAL
        is_campaign = mode in gemini_analyzer.CAMPAIGN_MODES

        emit_progress(channel, 1, TOTAL, "Fetching metadata & comments...")
        meta = source.get_metadata(src, with_comments=True)
        mentions = comment_analyzer.extract_timestamp_mentions(meta["comments"])
        set_job_has_comments(job_id, bool(mentions))

        emit_progress(channel, 2, TOTAL, "Downloading audio...",
                      f"{len(meta['comments'])} komentar · {len(mentions)} mention timestamp")
        audio_path = source.prepare_audio(src, video_id)

        # Analisis emosi audio — cepat (~10s), best-effort
        audio_emotion_segs: list[dict] = []
        try:
            from backend.analyzer.audio import analyze_audio as _analyze_audio
            ae_result = _analyze_audio(audio_path)
            audio_emotion_segs = ae_result.get("segments", [])
            logger.info("Audio emotion: %d segmen, dominan=%s",
                        len(audio_emotion_segs), ae_result.get("summary", {}).get("dominant_emotion"))
        except Exception as _ae_exc:
            logger.warning("Audio emotion analysis dilewati: %s", _ae_exc)

        # Analisis ekspresi wajah — hanya untuk file lokal (terlalu lambat untuk stream)
        face_segs: list[dict] = []
        from db.database import get_job_by_id as _gjbi
        _j = _gjbi(job_id)
        local_video_path = _j.get("file_path") if _j else None
        if local_video_path and os.path.exists(local_video_path):
            try:
                from backend.analyzer.expressions import analyze_expressions as _analyze_expr
                expr_result = _analyze_expr(local_video_path)
                face_segs = expr_result.get("segments", [])
                logger.info("Face expressions: %d segmen", len(face_segs))
            except Exception as _fe_exc:
                logger.warning("Face expression analysis dilewati: %s", _fe_exc)

        signals = {
            "face":          face_segs,
            "audio_emotions": audio_emotion_segs,
        }

        emit_progress(channel, 3, TOTAL, "Transcribing (Whisper)...")
        transcript = transcribe(audio_path, language=language)
        if not transcript["segments"]:
            raise Exception("Transcript kosong — video musik atau instrumental tidak didukung.")

        emit_progress(channel, 4, TOTAL, "Building natural windows...",
                      f"{len(transcript['segments'])} segmen · bahasa: {transcript['language']}")
        pauses = audio_analyzer.detect_pauses(audio_path)
        windows = build_windows(transcript["segments"], pauses=pauses)

        emit_progress(channel, 5, TOTAL, "Scoring signals...",
                      f"{len(windows)} window natural")
        windows = comment_analyzer.score_windows(windows, mentions, meta["chapters"])
        windows = audio_analyzer.score_windows(audio_path, windows)
        windows = narrative_analyzer.score_windows(windows)
        windows = text_analyzer.score_windows(windows, allow_ads=is_campaign)
        top_windows, fusion_stats = fuse(windows)
        save_segments(job_id, top_windows)

        if not top_windows:
            raise Exception("Semua window tersaring habis.")

        emit_progress(channel, 6, TOTAL, "AI judging (Gemini)...",
                      f"{fusion_stats['selected']} window dikirim ke AI")
        raw = analyze_gemini(
            top_windows,
            transcript["segments"],
            title,
            transcript["language"],
            min_seconds=min_seconds,
            max_seconds=max_seconds,
            performance_hint=performance_hint,
            mode=mode,
            campaign=campaign if campaign.get("product") else None,
            signals=signals,
        )
        word_data = transcript.get("words") or transcript.get("segments", [])
        candidates, _ = validate(
            raw,
            word_data,
            top_windows,
            min_seconds=min_seconds,
            max_seconds=max_seconds,
            campaign_mode=is_campaign,
        )
        save_candidates(job_id, candidates)
        cleanup_tmp(video_id)
        update_job_status(job_id, "done")
        emit_done(channel)

    except Exception as exc:
        logger.exception("Pipeline error job_id=%d", job_id)
        try:
            from db.database import update_job_status
            update_job_status(job_id, "error")
        except Exception:
            pass
        emit_error(channel, str(exc))


# ---------------------------------------------------------------- endpoints

@router.get("", response_model=list[JobResponse])
def list_jobs():
    from db.database import get_all_jobs, get_candidates_by_job
    jobs = get_all_jobs()
    result = []
    for job in jobs:
        candidates = get_candidates_by_job(job["id"])
        result.append(_job_to_response(job, len(candidates)))
    return result


@router.delete("/{job_id}")
def delete_job(job_id: int):
    """Hapus job, semua candidate-nya, dan file video yang sudah didownload."""
    import shutil
    from db.database import get_job_by_id, get_candidates_by_job
    from utils.helpers import sanitize_filename
    from utils.constants import OUTPUT_DIR

    job = get_job_by_id(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job tidak ditemukan")

    # Hapus semua file kandidat yang sudah didownload
    candidates = get_candidates_by_job(job_id)
    for cand in candidates:
        path = cand.get("output_path")
        if path and os.path.isfile(path):
            try:
                os.remove(path)
            except OSError:
                pass

    # Hapus folder output job (jika ada dan kosong atau hanya berisi file job ini)
    if job.get("title"):
        job_folder = os.path.join(OUTPUT_DIR, sanitize_filename(job["title"]))
        if os.path.isdir(job_folder):
            try:
                shutil.rmtree(job_folder)
            except OSError:
                pass

    # Hapus dari database
    from db.database import _connect
    with _connect() as conn:
        conn.execute("DELETE FROM candidates WHERE job_id = ?", (job_id,))
        conn.execute("DELETE FROM segments WHERE job_id = ?", (job_id,))
        conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))

    return {"deleted": True, "job_id": job_id}


@router.get("/{job_id}", response_model=JobResponse)
def get_job(job_id: int):
    from db.database import get_candidates_by_job, get_job_by_id
    job = get_job_by_id(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job tidak ditemukan")
    candidates = get_candidates_by_job(job_id)
    return _job_to_response(job, len(candidates))


@router.post("/metadata", response_model=MetadataResponse)
def get_metadata(body: dict):
    """Fetch metadata video tanpa memproses — untuk preview sebelum analyze."""
    from core import source
    src = body.get("src", "").strip()
    if not src:
        raise HTTPException(status_code=400, detail="src wajib diisi")
    src = source.normalize(src)
    problem = source.validate(src)
    if problem:
        raise HTTPException(status_code=400, detail=problem)
    try:
        meta = source.get_metadata(src)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return MetadataResponse(
        video_id=meta["video_id"],
        title=meta["title"],
        duration=meta.get("duration") or 0,
        thumbnail=meta.get("thumbnail"),
        resolution=meta.get("resolution"),
        uploader=meta.get("uploader"),
        source_type=source.detect_type(src),
    )


@router.post("", response_model=dict)
def create_and_process(req: ProcessRequest, background_tasks: BackgroundTasks):
    """Buat job baru dan jalankan pipeline di background."""
    from core import source, gemini_analyzer
    from core.clip_report import parse_hashtags
    from db.database import (
        create_job,
        get_job_by_url,
        get_yt_videos,
        update_job_status,
    )
    from core.channel_advisor import build_performance_hint

    src = source.normalize(req.src)
    problem = source.validate(src)
    if problem:
        raise HTTPException(status_code=400, detail=problem)

    try:
        meta = source.get_metadata(src)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    campaign = {
        "product": req.campaign_product,
        "benefit": req.campaign_benefit,
        "cta": req.campaign_cta,
        "hashtags": req.campaign_hashtags,
    }

    performance_hint = None
    if req.use_performance_hint:
        performance_hint = build_performance_hint(get_yt_videos())

    job_id = create_job(
        src,
        meta["video_id"],
        meta["title"],
        meta.get("duration"),
        meta.get("thumbnail"),
        source_type=source.detect_type(src),
        mode=req.mode,
        campaign_product=req.campaign_product,
        campaign_hashtags=req.campaign_hashtags,
    )
    update_job_status(job_id, "processing")

    background_tasks.add_task(
        _run_pipeline,
        job_id=job_id,
        src=src,
        video_id=meta["video_id"],
        title=meta["title"],
        language=req.language,
        min_seconds=req.min_seconds,
        max_seconds=req.max_seconds,
        performance_hint=performance_hint,
        mode=req.mode,
        campaign=campaign,
    )

    return {"job_id": job_id, "video_id": meta["video_id"], "title": meta["title"]}


@router.post("/{job_id}/reanalyze", response_model=dict)
def reanalyze(job_id: int, req: ProcessRequest, background_tasks: BackgroundTasks):
    """Analisis ulang job yang sudah ada — timpa kandidat lama."""
    from db.database import get_job_by_id, update_job_status
    from core import source

    job = get_job_by_id(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job tidak ditemukan")
    src = job["url"]

    campaign = {
        "product": req.campaign_product,
        "benefit": req.campaign_benefit,
        "cta": req.campaign_cta,
        "hashtags": req.campaign_hashtags,
    }

    update_job_status(job_id, "processing")
    background_tasks.add_task(
        _run_pipeline,
        job_id=job_id,
        src=src,
        video_id=job["video_id"],
        title=job["title"],
        language=req.language,
        min_seconds=req.min_seconds,
        max_seconds=req.max_seconds,
        performance_hint=None,
        mode=req.mode,
        campaign=campaign,
    )
    return {"job_id": job_id}


# ---------------------------------------------------------------- websocket

@router.websocket("/ws/{job_id}")
async def pipeline_ws(job_id: int, websocket: WebSocket):
    channel = f"job_{job_id}"
    await manager.connect(channel, websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(channel, websocket)
