"""Router: candidates — list, update status, download clip."""

from __future__ import annotations

import logging
import os

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from backend.schemas import CandidateResponse, DownloadClipRequest, UpdateCandidateStatusRequest

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/candidates", tags=["candidates"])


# ---------------------------------------------------------------- helpers

def _cand_to_response(c: dict) -> CandidateResponse:
    return CandidateResponse(
        id=c.get("id"),
        start=c.get("start", ""),
        end=c.get("end", ""),
        start_seconds=c.get("start_seconds") or 0,
        duration_seconds=c.get("duration_seconds") or 0,
        final_score=c.get("final_score") or 0,
        clip_type=c.get("clip_type"),
        hook=c.get("hook"),
        reason=c.get("reason"),
        title=c.get("title"),
        description=c.get("description"),
        shareability=c.get("shareability"),
        discussion_trigger=c.get("discussion_trigger"),
        standalone_context=c.get("standalone_context"),
        hook_strength=c.get("hook_strength"),
        payoff_strength=c.get("payoff_strength"),
        hook_start=c.get("hook_start"),
        hook_end=c.get("hook_end"),
        hook_placement=c.get("hook_placement"),
        campaign_fit=c.get("campaign_fit"),
        product_moment=c.get("product_moment"),
        thumbnail_prompt=c.get("thumbnail_prompt"),
        thumbnail_text=c.get("thumbnail_text"),
        segment_scores=c.get("segment_scores"),
        signal_notes=c.get("signal_notes"),
        status=c.get("status"),
        output_path=c.get("output_path"),
        parts=c.get("parts"),
    )


# ---------------------------------------------------------------- endpoints

@router.get("/job/{job_id}", response_model=list[CandidateResponse])
def list_candidates(job_id: int):
    from db.database import get_candidates_by_job, get_job_by_id
    if not get_job_by_id(job_id):
        raise HTTPException(status_code=404, detail="Job tidak ditemukan")
    candidates = get_candidates_by_job(job_id)
    return [_cand_to_response(c) for c in candidates]


@router.get("/{candidate_id}")
def get_candidate(candidate_id: int):
    from db.database import _connect
    with _connect() as conn:
        row = conn.execute(
            """SELECT c.*, j.title AS video_title, j.url, j.source_type, j.video_id AS job_video_id,
                      j.mode, j.campaign_product
               FROM candidates c JOIN jobs j ON c.job_id = j.id WHERE c.id = ?""",
            (candidate_id,),
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Kandidat tidak ditemukan")
    import json
    cand = dict(row)
    for field in ("signal_notes", "segment_scores", "parts", "people"):
        try:
            cand[field] = json.loads(cand.get(field) or "null")
        except Exception:
            cand[field] = None
    return cand


@router.patch("/{candidate_id}/status")
def update_status(candidate_id: int, body: UpdateCandidateStatusRequest):
    from db.database import update_candidate_status
    allowed = {"pending", "downloaded", "skipped"}
    if body.status not in allowed:
        raise HTTPException(status_code=400, detail=f"Status harus salah satu: {allowed}")
    update_candidate_status(candidate_id, body.status)
    return {"ok": True}


@router.post("/{candidate_id}/download")
def download_clip(candidate_id: int, body: DownloadClipRequest):
    """Potong video sesuai timestamps kandidat dan simpan ke output folder."""
    from db.database import get_candidates_by_job, get_job_by_id, update_candidate_status
    from core import source
    from core.clip_report import build_report, write_report
    from utils.helpers import sanitize_filename, seconds_to_timestamp
    from utils.constants import OUTPUT_DIR

    job = get_job_by_id(body.job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job tidak ditemukan")

    candidates = get_candidates_by_job(body.job_id)
    cand = next((c for c in candidates if c.get("id") == candidate_id), None)
    if not cand:
        raise HTTPException(status_code=404, detail="Kandidat tidak ditemukan")

    parts = cand.get("parts") or [{
        "start": cand["start"],
        "end": cand["end"],
        "start_seconds": cand["start_seconds"],
        "duration_seconds": cand["duration_seconds"],
    }]

    src = job["url"]
    rank = next(
        (i + 1 for i, c in enumerate(candidates) if c.get("id") == candidate_id),
        candidate_id,
    )

    output_folder = os.path.join(OUTPUT_DIR, sanitize_filename(job["title"]))
    os.makedirs(output_folder, exist_ok=True)

    start_label = parts[0]["start"].replace(":", "-")
    output_path = os.path.join(output_folder, f"clip_{rank:02d}_{start_label}.mp4")

    try:
        result_path = source.extract_clip(src, parts, output_path)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Download gagal: {exc}")

    # Tulis laporan analisis di sebelah video
    try:
        campaign_product = job.get("campaign_product") or ""
        campaign = {"product": campaign_product} if campaign_product else None
        report_path = write_report(
            result_path,
            build_report(cand, job["title"], source.describe(src), rank, campaign),
        )
    except Exception:
        report_path = None

    update_candidate_status(candidate_id, "downloaded", result_path)

    return {
        "output_path": result_path,
        "report_path": report_path,
        "filename": os.path.basename(result_path),
    }


@router.get("/{candidate_id}/file")
def serve_clip_file(candidate_id: int):
    """Serve file video yang sudah didownload langsung ke browser."""
    from db.database import _connect

    with _connect() as conn:
        row = conn.execute(
            "SELECT output_path FROM candidates WHERE id = ?", (candidate_id,)
        ).fetchone()

    if not row or not row["output_path"]:
        raise HTTPException(status_code=404, detail="File belum didownload")

    path = row["output_path"]
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="File tidak ditemukan di disk")

    return FileResponse(path, media_type="video/mp4", filename=os.path.basename(path))


@router.get("/{candidate_id}/preview")
def candidate_preview(candidate_id: int):
    """Quick preview video clip — stream copy (cepat, tanpa re-encode).

    Untuk sumber lokal: potong langsung dari file asli.
    Untuk YouTube: kembalikan info waktu saja (browser pakai embed YouTube).
    """
    from db.database import get_job_by_id, _connect
    from core import source

    with _connect() as conn:
        row = conn.execute(
            """SELECT c.*, j.url, j.source_type, j.video_id AS job_video_id
               FROM candidates c JOIN jobs j ON c.job_id = j.id WHERE c.id = ?""",
            (candidate_id,),
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Kandidat tidak ditemukan.")

    cand = dict(row)
    src  = cand["url"]
    start_s   = float(cand.get("start_seconds") or 0)
    end_s     = float(cand.get("end_seconds") or start_s)
    duration_s = end_s - start_s

    # Jika sudah ada output_path (sudah didownload), serve langsung
    if cand.get("output_path") and os.path.exists(cand["output_path"]):
        from fastapi.responses import FileResponse
        return FileResponse(cand["output_path"], media_type="video/mp4")

    # Sumber lokal: buat preview cepat
    if source.detect_type(src) == source.LOCAL:
        import json
        parts = json.loads(cand.get("parts") or "null") or [{
            "start": cand.get("start") or "",
            "end":   cand.get("end") or "",
            "start_seconds": start_s,
            "duration_seconds": duration_s,
        }]
        job = get_job_by_id(cand["job_id"])
        video_id = job["video_id"] if job else str(cand["job_id"])
        try:
            preview_path = source.make_preview(src, parts, video_id, candidate_id)
            from fastapi.responses import FileResponse
            return FileResponse(preview_path, media_type="video/mp4")
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Preview gagal: {exc}")

    # YouTube: kembalikan metadata waktu saja
    return {
        "type": "youtube",
        "url": src,
        "start_seconds": start_s,
        "duration_seconds": duration_s,
        "video_id": cand.get("job_video_id") or "",
    }


@router.delete("/{candidate_id}/file")
def delete_candidate_file(candidate_id: int):
    """Hapus file video yang sudah didownload, reset status ke pending."""
    from db.database import _connect
    with _connect() as conn:
        row = conn.execute(
            "SELECT output_path FROM candidates WHERE id = ?", (candidate_id,)
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Candidate tidak ditemukan")

        path = row["output_path"]
        if path and os.path.isfile(path):
            os.remove(path)

        conn.execute(
            "UPDATE candidates SET output_path = NULL, status = 'pending' WHERE id = ?",
            (candidate_id,)
        )
    return {"deleted": True, "candidate_id": candidate_id}


@router.get("/{candidate_id}/caption")
def get_caption(candidate_id: int):
    """Return caption siap-tempel untuk kandidat ini."""
    from db.database import _connect
    from core.clip_report import build_caption, build_hashtags

    with _connect() as conn:
        row = conn.execute(
            """
            SELECT c.*, j.title as video_title, j.url, j.campaign_product,
                   j.campaign_hashtags
            FROM candidates c
            JOIN jobs j ON c.job_id = j.id
            WHERE c.id = ?
            """,
            (candidate_id,),
        ).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Kandidat tidak ditemukan")

    import json
    cand = dict(row)
    for field in ("signal_notes", "segment_scores", "hashtags", "parts", "people"):
        try:
            cand[field] = json.loads(cand.get(field) or "null") or {}
        except Exception:
            cand[field] = {}

    campaign_product = cand.get("campaign_product") or ""
    try:
        campaign_hashtags = json.loads(cand.get("campaign_hashtags") or "[]")
    except Exception:
        campaign_hashtags = []

    campaign = {"product": campaign_product, "hashtags": campaign_hashtags} if campaign_product else None

    from core import source
    src_desc = source.describe(cand["url"])

    return {
        "title": cand.get("title"),
        "description": cand.get("description"),
        "hashtags": build_hashtags(cand, campaign),
        "caption": build_caption(cand, cand["video_title"], campaign),
    }
