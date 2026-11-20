"""Router: download — video utuh dan potong manual."""

from __future__ import annotations

import logging
import os

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from backend.schemas import FullDownloadRequest, ManualClipRequest, MetadataResponse

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/download", tags=["download"])


# ---------------------------------------------------------------- endpoints

@router.post("/metadata", response_model=MetadataResponse)
def get_metadata(body: dict):
    """Fetch metadata tanpa memproses — untuk halaman Download Manual."""
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


@router.post("/full")
def download_full(body: FullDownloadRequest):
    """Unduh video utuh ke output folder."""
    from core import source
    from utils.helpers import sanitize_filename
    from utils.constants import OUTPUT_DIR

    src = source.normalize(body.src)
    problem = source.validate(src)
    if problem:
        raise HTTPException(status_code=400, detail=problem)

    if not source.needs_full_download(src):
        return {
            "output_path": src,
            "message": "File lokal tidak perlu diunduh — sudah ada di disk.",
            "local": True,
        }

    output_folder = os.path.join(OUTPUT_DIR, sanitize_filename(body.title))
    os.makedirs(output_folder, exist_ok=True)
    output_path = os.path.join(output_folder, f"full_{sanitize_filename(body.title)}.mp4")

    try:
        result = source.download_full(src, output_path)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Download gagal: {exc}")

    size_mb = round(os.path.getsize(result) / 1e6, 1)
    return {"output_path": result, "size_mb": size_mb, "local": False}


@router.post("/clip")
def manual_clip(body: ManualClipRequest):
    """Potong video sesuai timestamp yang diisi user."""
    from core import source
    from utils.helpers import sanitize_filename, seconds_to_timestamp, timestamp_to_seconds
    from utils.constants import OUTPUT_DIR

    src = source.normalize(body.src)
    problem = source.validate(src)
    if problem:
        raise HTTPException(status_code=400, detail=problem)

    try:
        start_seconds = timestamp_to_seconds(body.start)
        end_seconds = timestamp_to_seconds(body.end)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"{exc}. Pakai format HH:MM:SS atau MM:SS.")

    if end_seconds <= start_seconds:
        raise HTTPException(status_code=400, detail="Waktu selesai harus setelah waktu mulai.")

    # Validasi terhadap durasi video kalau metadata tersedia
    try:
        meta = source.get_metadata(src)
        duration = meta.get("duration") or 0
        if duration and start_seconds >= duration:
            raise HTTPException(
                status_code=400,
                detail=f"Waktu mulai melewati durasi video ({seconds_to_timestamp(duration)}).",
            )
        if duration and end_seconds > duration:
            end_seconds = duration
    except HTTPException:
        raise
    except Exception:
        pass

    parts = [{
        "start": body.start,
        "end": body.end,
        "start_seconds": start_seconds,
        "duration_seconds": end_seconds - start_seconds,
    }]

    output_folder = os.path.join(OUTPUT_DIR, sanitize_filename(body.title))
    os.makedirs(output_folder, exist_ok=True)
    filename = f"manual_{body.start.replace(':', '-')}_{body.end.replace(':', '-')}.mp4"
    output_path = os.path.join(output_folder, filename)

    try:
        result = source.extract_clip(src, parts, output_path)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Potong manual gagal: {exc}")

    return {
        "output_path": result,
        "filename": os.path.basename(result),
        "duration_seconds": end_seconds - start_seconds,
    }


@router.get("/file")
def serve_file(path: str):
    """Serve file video dari output folder ke browser."""
    from utils.constants import OUTPUT_DIR
    # Validasi path ada di dalam OUTPUT_DIR (cegah path traversal)
    abs_path = os.path.realpath(path)
    abs_output = os.path.realpath(OUTPUT_DIR)
    if not abs_path.startswith(abs_output):
        raise HTTPException(status_code=403, detail="Akses ditolak")
    if not os.path.isfile(abs_path):
        raise HTTPException(status_code=404, detail="File tidak ditemukan")
    return FileResponse(abs_path, media_type="video/mp4", filename=os.path.basename(abs_path))
