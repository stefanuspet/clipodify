"""Upload video ke YouTube + jadwal tayang (Fase 9-1).

Protokol upload resumable YouTube Data API v3, dengan `requests` (tanpa dependensi baru).
Video selalu diupload PRIVATE; jadwal memakai `status.publishAt` sehingga YouTube sendiri
yang mempublikasikan tepat waktu (komputer tidak perlu menyala).

PENTING: proyek Google Cloud yang belum lolos audit YouTube API membuat video hasil
upload API terkunci private — jadwal publik tidak akan jalan sampai audit lolos.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone

import requests

logger = logging.getLogger(__name__)

UPLOAD_SCOPE = "https://www.googleapis.com/auth/youtube.upload"
UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"
CHUNK = 8 * 1024 * 1024
MIN_SCHEDULE_AHEAD = timedelta(minutes=15)
CATEGORY_PEOPLE_BLOGS = "22"


class UploadError(Exception):
    pass


def has_upload_scope(creds) -> bool:
    scopes = set(getattr(creds, "scopes", None) or [])
    granted = set(getattr(creds, "granted_scopes", None) or [])
    return UPLOAD_SCOPE in scopes or UPLOAD_SCOPE in granted


def build_metadata(title: str, description: str, tags: list[str], publish_at: datetime | None) -> dict:
    title = " ".join((title or "").split())[:100]
    if not title:
        raise UploadError("Judul wajib diisi.")
    if "<" in title or ">" in title:
        raise UploadError("Judul tidak boleh mengandung < atau > (ditolak YouTube).")
    description = (description or "").replace("<", "‹").replace(">", "›")[:5000]
    clean_tags, total = [], 0
    for t in tags or []:
        t = t.strip().lstrip("#")
        if t and total + len(t) + 1 <= 480:
            clean_tags.append(t)
            total += len(t) + 1
    status = {"privacyStatus": "private", "selfDeclaredMadeForKids": False}
    if publish_at:
        if publish_at.tzinfo is None:
            raise UploadError("Waktu jadwal harus menyertakan zona waktu.")
        if publish_at - datetime.now(timezone.utc) < MIN_SCHEDULE_AHEAD:
            raise UploadError("Jadwal tayang minimal 15 menit dari sekarang.")
        status["publishAt"] = publish_at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    return {"snippet": {"title": title, "description": description, "tags": clean_tags,
                        "categoryId": CATEGORY_PEOPLE_BLOGS},
            "status": status}


def _explain(resp: requests.Response) -> str:
    try:
        err = resp.json().get("error", {})
        reason = (err.get("errors") or [{}])[0].get("reason", "")
        msg = err.get("message", resp.text[:200])
    except ValueError:
        reason, msg = "", resp.text[:200]
    if reason in ("insufficientPermissions", "forbidden") or resp.status_code == 403 and "scope" in msg.lower():
        return "Izin upload belum diberikan. Hubungkan ulang akun YouTube di halaman YouTube."
    if reason in ("quotaExceeded", "uploadLimitExceeded"):
        return "Kuota upload YouTube hari ini habis. Coba lagi besok."
    if reason == "invalidPublishAt":
        return "Waktu jadwal ditolak YouTube (harus di masa depan dan video harus private)."
    return f"YouTube menolak upload ({resp.status_code} {reason}): {msg}"


def upload(creds, file_path: str, metadata: dict, progress=None) -> dict:
    """Upload resumable. progress(bytes_sent, total). Return {video_id, ...}."""
    if not os.path.exists(file_path):
        raise UploadError("File video tidak ditemukan.")
    size = os.path.getsize(file_path)
    headers = {"Authorization": f"Bearer {creds.token}", "Content-Type": "application/json; charset=UTF-8",
               "X-Upload-Content-Type": "video/mp4", "X-Upload-Content-Length": str(size)}
    r = requests.post(UPLOAD_URL, params={"uploadType": "resumable", "part": "snippet,status"},
                      headers=headers, data=json.dumps(metadata), timeout=60)
    if r.status_code != 200 or "Location" not in r.headers:
        raise UploadError(_explain(r))
    session = r.headers["Location"]

    sent = 0
    with open(file_path, "rb") as f:
        while sent < size:
            f.seek(sent)
            chunk = f.read(CHUNK)
            end = sent + len(chunk) - 1
            resp = None
            for attempt in range(4):
                try:
                    resp = requests.put(session, data=chunk, timeout=300, headers={
                        "Authorization": f"Bearer {creds.token}",
                        "Content-Length": str(len(chunk)),
                        "Content-Range": f"bytes {sent}-{end}/{size}"})
                except requests.RequestException as e:
                    logger.warning("Upload chunk gagal (%s), coba lagi", e)
                    resp = None
                if resp is not None and resp.status_code in (200, 201, 308):
                    break
                if resp is not None and resp.status_code not in (500, 502, 503, 504):
                    raise UploadError(_explain(resp))
            else:
                raise UploadError("Upload terputus berulang kali. Periksa koneksi lalu coba lagi.")
            if resp.status_code in (200, 201):
                body = resp.json()
                if progress:
                    progress(size, size)
                return {"video_id": body["id"], "privacy": body.get("status", {}).get("privacyStatus"),
                        "publish_at": body.get("status", {}).get("publishAt"),
                        "upload_status": body.get("status", {}).get("uploadStatus")}
            rng = resp.headers.get("Range")              # 308: server menyebut byte terakhir yang diterima
            sent = int(rng.split("-")[1]) + 1 if rng else sent + len(chunk)
            if progress:
                progress(sent, size)
    raise UploadError("Upload selesai tanpa jawaban dari YouTube.")
