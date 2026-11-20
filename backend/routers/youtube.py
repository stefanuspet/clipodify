"""Router: YouTube Analytics — connect, data, saran."""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import RedirectResponse

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/youtube", tags=["youtube"])

# Redirect ke sini setelah Google OAuth selesai
_CALLBACK_PATH = "/api/youtube/callback"


# ---------------------------------------------------------------- status

@router.get("/status")
def get_status():
    from core.youtube_analytics import is_connected
    from db.database import get_yt_auth
    if not is_connected():
        return {"connected": False}
    auth = get_yt_auth()
    upload_scope = False
    try:
        from core.youtube_analytics import load_credentials
        from core.youtube_upload import has_upload_scope
        upload_scope = has_upload_scope(load_credentials())
    except Exception:
        pass
    return {
        "connected": True,
        "channel_title": auth["channel_title"],
        "channel_id": auth["channel_id"],
        "connected_at": auth["connected_at"],
        "upload_scope": upload_scope,
    }


# ---------------------------------------------------------------- OAuth2 flow

@router.post("/auth-url")
def get_auth_url(body: dict):
    """Return URL Google OAuth untuk diarahkan user dari frontend.

    `client_secrets` dikirim sebagai string JSON dari frontend (user paste isinya)
    atau sebagai `secrets_path` (path file di server). Keduanya didukung.
    """
    from google_auth_oauthlib.flow import Flow
    from core.youtube_analytics import SCOPES

    secrets_path = (body.get("secrets_path") or "").strip().strip("'\"")
    secrets_json = body.get("secrets_json")

    if not secrets_path and not secrets_json:
        raise HTTPException(
            status_code=400,
            detail="Isi secrets_path (path file) atau secrets_json (isi JSON-nya).",
        )

    base_url = (body.get("base_url") or "http://localhost:8000").rstrip("/")
    redirect_uri = f"{base_url}{_CALLBACK_PATH}"

    try:
        if secrets_path:
            flow = Flow.from_client_secrets_file(secrets_path, SCOPES, redirect_uri=redirect_uri)
            with open(secrets_path, "r", encoding="utf-8") as f:
                secrets_raw = f.read()
        else:
            flow = Flow.from_client_config(
                secrets_json if isinstance(secrets_json, dict) else json.loads(secrets_json),
                SCOPES,
                redirect_uri=redirect_uri,
            )
            secrets_raw = (
                json.dumps(secrets_json) if isinstance(secrets_json, dict) else secrets_json
            )

        auth_url, state = flow.authorization_url(
            access_type="offline",
            include_granted_scopes="true",
            prompt="consent",
        )
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Gagal membuat OAuth URL: {exc}")

    # Simpan secrets_raw sementara di state agar callback bisa merekonstruksi flow
    # Karena state hanya string, simpan ke file tmp dengan nama = state
    import os
    from utils.constants import TMP_DIR
    state_file = os.path.join(TMP_DIR, f"yt_oauth_{state}.json")
    os.makedirs(TMP_DIR, exist_ok=True)
    with open(state_file, "w", encoding="utf-8") as f:
        json.dump({
            "secrets": secrets_raw,
            "redirect_uri": redirect_uri,
            # PKCE: library membuat code_verifier otomatis; callback wajib memakai yang sama
            "code_verifier": getattr(flow, "code_verifier", None),
        }, f)

    return {"auth_url": auth_url, "state": state}


@router.get("/callback")
def oauth_callback(code: str, state: str, frontend_url: str = "http://localhost:5173"):
    """Google redirect ke sini setelah user approve. Simpan token lalu redirect ke frontend."""
    import os
    from google_auth_oauthlib.flow import Flow
    from core.youtube_analytics import SCOPES, _fetch_channel
    from db.database import save_yt_auth
    from utils.constants import TMP_DIR

    state_file = os.path.join(TMP_DIR, f"yt_oauth_{state}.json")
    if not os.path.exists(state_file):
        raise HTTPException(status_code=400, detail="State tidak valid atau sudah kedaluwarsa.")

    with open(state_file, "r", encoding="utf-8") as f:
        saved = json.load(f)
    os.remove(state_file)

    secrets = saved["secrets"]
    redirect_uri = saved["redirect_uri"]

    try:
        secrets_dict = json.loads(secrets) if isinstance(secrets, str) else secrets
        # Google bisa mengembalikan scope gabungan (izin lama + baru); jangan dianggap error
        os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")
        flow = Flow.from_client_config(secrets_dict, SCOPES, redirect_uri=redirect_uri)
        if saved.get("code_verifier"):
            flow.code_verifier = saved["code_verifier"]
        flow.fetch_token(code=code)
        creds = flow.credentials
        channel = _fetch_channel(creds)
        save_yt_auth(secrets, creds.to_json(), channel["channel_id"], channel["channel_title"])
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"OAuth gagal: {exc}")

    return RedirectResponse(url=f"{frontend_url}/youtube?connected=1")


@router.post("/reconnect-url")
def reconnect_url(body: dict | None = None):
    """URL OAuth memakai client secret yang SUDAH tersimpan — untuk menambah izin upload."""
    from db.database import get_yt_auth
    auth = get_yt_auth()
    if not auth:
        raise HTTPException(status_code=400, detail="Belum ada koneksi YouTube yang tersimpan.")
    return get_auth_url({"secrets_json": auth["client_secrets"],
                         "base_url": (body or {}).get("base_url") or "http://localhost:8000"})


@router.delete("/disconnect")
def disconnect():
    from db.database import delete_yt_auth
    delete_yt_auth()
    return {"ok": True}


# ---------------------------------------------------------------- videos

@router.get("/videos")
def get_videos():
    from db.database import get_yt_videos
    return get_yt_videos()


@router.post("/videos/refresh")
def refresh_videos(body: dict):
    from core.youtube_analytics import (
        fetch_video_metrics,
        get_channel,
        is_connected,
        list_videos,
        load_credentials,
    )
    from db.database import save_yt_videos

    if not is_connected():
        raise HTTPException(status_code=401, detail="Belum terhubung ke YouTube.")

    limit = int(body.get("limit", 50))
    try:
        creds = load_credentials()
        channel = get_channel(creds)
        videos = list_videos(creds, channel["uploads_playlist"], limit=limit)
        if not videos:
            return {"count": 0, "message": "Channel belum punya video."}
        metrics = fetch_video_metrics(creds, [v["video_id"] for v in videos])
        for video in videos:
            video.update(metrics.get(video["video_id"], {}))
        save_yt_videos(videos)
        return {"count": len(videos)}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/retention")
def fetch_retention(body: dict):
    from core.youtube_analytics import fetch_retention_curve, is_connected, load_credentials
    from db.database import save_yt_retention

    if not is_connected():
        raise HTTPException(status_code=401, detail="Belum terhubung ke YouTube.")

    video_id = (body.get("video_id") or "").strip()
    if not video_id:
        raise HTTPException(status_code=400, detail="video_id wajib diisi.")

    try:
        creds = load_credentials()
        curve = fetch_retention_curve(creds, video_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    if not curve:
        return {"video_id": video_id, "curve": [], "message": "Data retensi belum tersedia."}

    save_yt_retention(video_id, curve)
    return {"video_id": video_id, "curve": curve}


# ---------------------------------------------------------------- audience

@router.get("/audience")
def get_audience():
    from core.youtube_analytics import fetch_audience, is_connected, load_credentials

    if not is_connected():
        raise HTTPException(status_code=401, detail="Belum terhubung ke YouTube.")

    try:
        creds = load_credentials()
        return fetch_audience(creds)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ---------------------------------------------------------------- advice

@router.post("/advice/clip")
def advice_clip():
    from core.channel_advisor import advise
    from core.youtube_analytics import is_connected
    from db.database import get_yt_auth, get_yt_videos

    if not is_connected():
        raise HTTPException(status_code=401, detail="Belum terhubung ke YouTube.")

    videos = get_yt_videos()
    if not videos:
        raise HTTPException(status_code=422, detail="Belum ada data video. Tarik dulu.")

    auth = get_yt_auth()
    try:
        text = advise(videos, {"channel_title": auth["channel_title"]})
        return {"advice": text}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/advice/setup")
def advice_setup(body: dict):
    from core.channel_advisor import advise_setup
    from core.youtube_analytics import (
        fetch_setup_snapshot,
        is_connected,
        load_credentials,
    )

    if not is_connected():
        raise HTTPException(status_code=401, detail="Belum terhubung ke YouTube.")

    limit = int(body.get("limit", 50))
    try:
        creds = load_credentials()
        snapshot = fetch_setup_snapshot(creds, limit=limit)
        text = advise_setup(snapshot)
        return {"advice": text}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/advice/schedule")
def advice_schedule():
    from core.upload_schedule import advise
    from core.youtube_analytics import is_connected
    from db.database import get_yt_videos

    if not is_connected():
        raise HTTPException(status_code=401, detail="Belum terhubung ke YouTube.")

    videos = get_yt_videos()
    if not videos:
        raise HTTPException(status_code=422, detail="Belum ada data video. Tarik dulu.")

    try:
        text = advise(videos)
        return {"advice": text}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/schedule-stats")
def schedule_stats():
    from core.upload_schedule import analyze
    from db.database import get_yt_videos
    videos = get_yt_videos()
    return analyze(videos)


# ---------------------------------------------------------------- upload & jadwal (Fase 9-1)

_upload_threads: dict[int, object] = {}


def _render_info(render_id: str) -> dict:
    """File & kandidat dari sebuah render (masih di memori, atau tercatat di edit_renders)."""
    from backend.routers.editor import _active_renders
    from db.database import list_edit_renders
    info = _active_renders.get(render_id) or {}
    if info.get("output_path"):
        return {"file_path": info["output_path"], "candidate_id": info.get("candidate_id"),
                "music": info.get("music")}
    rec = next((r for r in list_edit_renders() if r["render_id"] == render_id), None)
    if rec and rec.get("output_path"):
        music = None
        try:
            with open(rec["plan_file"], encoding="utf-8") as f:
                music = (json.load(f).get("render") or {}).get("music")
        except (OSError, ValueError, TypeError):
            pass
        return {"file_path": rec["output_path"], "candidate_id": rec["candidate_id"], "music": music}
    raise HTTPException(status_code=404, detail="Render tidak ditemukan (sudah dihapus atau backend di-restart).")


@router.get("/upload/defaults")
def upload_defaults(render_id: str):
    """Isian awal form upload: judul/deskripsi/hashtag dari kandidat + kredit musik bila wajib."""
    import os
    from core.youtube_analytics import is_connected, load_credentials
    from core.youtube_upload import has_upload_scope
    from db.database import _connect
    from utils.constants import CHANNEL_TZ_OFFSET

    info = _render_info(render_id)
    title, description, tags, credit = _defaults_for(info)
    scope_ok, connected = False, is_connected()
    if connected:
        try:
            scope_ok = has_upload_scope(load_credentials())
        except Exception:
            connected = False
    return {"title": title[:100], "description": description, "tags": tags, "music_credit": credit,
            "file": os.path.basename(info["file_path"]), "connected": connected, "upload_scope": scope_ok,
            "tz_offset_hours": CHANNEL_TZ_OFFSET}


def _defaults_for(info: dict) -> tuple[str, str, list[str], str | None]:
    from db.database import _connect
    title = description = ""
    tags: list[str] = []
    if info.get("candidate_id"):
        with _connect() as conn:
            row = conn.execute("SELECT title, description, hashtags FROM candidates WHERE id = ?",
                               (info["candidate_id"],)).fetchone()
        if row:
            title, description = row["title"] or "", row["description"] or ""
            try:
                tags = json.loads(row["hashtags"] or "[]")
            except ValueError:
                tags = []
    credit = (info.get("music") or {}).get("credit")
    if credit:
        description = (description + "\n\n" + credit).strip()
    return title[:100], description, tags, credit


@router.post("/upload")
def start_upload(body: dict):
    """Upload render ke YouTube sebagai PRIVATE; opsional jadwal tayang (publish_at ISO + zona waktu)."""
    import threading
    from datetime import datetime
    from core.youtube_upload import UploadError, build_metadata
    from db.database import create_yt_upload

    info = _render_info(str(body.get("render_id") or ""))
    publish_at = None
    if body.get("publish_at"):
        try:
            publish_at = datetime.fromisoformat(str(body["publish_at"]).replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(status_code=400, detail="Format waktu jadwal tidak valid.")
    try:
        meta = build_metadata(body.get("title", ""), body.get("description", ""), body.get("tags") or [], publish_at)
    except UploadError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    upload_id = create_yt_upload({
        "render_id": body.get("render_id"), "candidate_id": info.get("candidate_id"),
        "file_path": info["file_path"], "title": meta["snippet"]["title"],
        "description": meta["snippet"]["description"], "tags": meta["snippet"]["tags"],
        "publish_at": meta["status"].get("publishAt")})
    t = threading.Thread(target=_run_upload, args=(upload_id, body.get("render_id"), info["file_path"], meta),
                         daemon=True)
    _upload_threads[upload_id] = t
    t.start()
    return {"upload_id": upload_id, "status": "queued"}


def _run_upload(upload_id: int, render_id: str | None, file_path: str, meta: dict) -> None:
    from datetime import datetime
    from core.youtube_analytics import load_credentials
    from core.youtube_upload import UploadError, has_upload_scope, upload
    from db.database import link_edit_render, update_yt_upload

    try:
        update_yt_upload(upload_id, status="uploading", progress=0)
        creds = load_credentials()
        if not has_upload_scope(creds):
            raise UploadError("Izin upload belum diberikan. Hubungkan ulang akun YouTube di halaman YouTube.")
        res = upload(creds, file_path, meta,
                     progress=lambda sent, total: update_yt_upload(upload_id, progress=round(sent / total, 3)))
        update_yt_upload(upload_id, status="scheduled" if meta["status"].get("publishAt") else "uploaded",
                         progress=1, video_id=res["video_id"],
                         uploaded_at=datetime.now().isoformat(timespec="seconds"))
        if render_id:
            link_edit_render(render_id, res["video_id"])     # langsung terhubung di halaman Learning
    except Exception as exc:  # pesan jelas ke UI, bukan diam
        logger.exception("Upload %s gagal", upload_id)
        update_yt_upload(upload_id, status="failed", error=str(exc)[:500])


@router.get("/uploads")
def uploads():
    from db.database import list_yt_uploads
    return list_yt_uploads()


# ---------------------------------------------------------------- saran slot tayang (Fase 9-2)

@router.get("/schedule/suggest")
def schedule_suggest(count: int = 4):
    """Slot tayang berikutnya dari data channel, berjarak aman dari video yang sudah tayang/terjadwal."""
    from datetime import datetime, timezone
    from core.upload_schedule import suggest_slots
    from db.database import get_yt_videos, list_yt_uploads

    videos, taken = _taken_slots()
    return suggest_slots(videos, taken, now=datetime.now(timezone.utc), count=max(1, min(count, 8)))


def _taken_slots(extra: list | None = None):
    from datetime import datetime
    from db.database import get_yt_videos, list_yt_uploads
    videos = get_yt_videos()
    taken = list(extra or [])
    for v in videos:
        if v.get("published_at"):
            taken.append(datetime.fromisoformat(v["published_at"].replace("Z", "+00:00")))
    for u in list_yt_uploads():
        if u.get("publish_at") and u["status"] in ("queued", "uploading", "scheduled"):
            taken.append(datetime.fromisoformat(u["publish_at"].replace("Z", "+00:00")))
    return videos, taken


# ---------------------------------------------------------------- antrean beberapa klip (Fase 9-3)

BATCH_MAX = 5          # 1 upload ≈ 1.600 unit kuota; default 10.000/hari


def _pending_renders() -> list[dict]:
    import os
    from db.database import list_edit_renders, list_yt_uploads
    busy = {u["render_id"] for u in list_yt_uploads() if u["status"] != "failed" and u.get("render_id")}
    out = []
    for r in list_edit_renders():
        if r.get("yt_video_id") or r["render_id"] in busy:
            continue
        if not r.get("output_path") or not os.path.exists(r["output_path"]):
            continue
        out.append(r)
    return out


@router.get("/batch/candidates")
def batch_candidates():
    """Render Auto Edit yang belum diupload (file masih ada)."""
    import os
    out = []
    for r in _pending_renders():
        title, *_ = _defaults_for({"candidate_id": r["candidate_id"], "music": None})
        out.append({"render_id": r["render_id"], "title": title or r.get("candidate_title") or "",
                    "created_at": r["created_at"], "file": os.path.basename(r["output_path"]),
                    "intent": r.get("intent")})
    return {"renders": out, "max": BATCH_MAX}


@router.post("/batch/preview")
def batch_preview(body: dict):
    """Bagikan slot berjarak aman ke render terpilih (urut sesuai pilihan)."""
    from datetime import datetime, timezone
    from core.upload_schedule import suggest_slots
    ids = list(dict.fromkeys(body.get("render_ids") or []))
    if not ids:
        raise HTTPException(status_code=400, detail="Pilih minimal satu render.")
    if len(ids) > BATCH_MAX:
        raise HTTPException(status_code=400, detail=f"Maksimal {BATCH_MAX} video per antrean (batas kuota upload harian).")
    videos, taken = _taken_slots()
    sug = suggest_slots(videos, taken, now=datetime.now(timezone.utc), count=len(ids), days=14)
    if len(sug["slots"]) < len(ids):
        raise HTTPException(status_code=400, detail="Slot berjarak aman tidak cukup dalam 14 hari ke depan.")
    pending = {r["render_id"]: r for r in _pending_renders()}
    items = []
    for rid, slot in zip(ids, sug["slots"]):
        if rid not in pending:
            raise HTTPException(status_code=400, detail=f"Render {rid[:8]} sudah diupload/dalam antrean atau filenya hilang.")
        title, *_ = _defaults_for({"candidate_id": pending[rid]["candidate_id"], "music": None})
        items.append({"render_id": rid, "title": title, "publish_local": slot["local"], "reason": slot["reason"]})
    return {"items": items, "notes": sug["notes"], "tz_offset_hours": sug["offset_hours"]}


@router.post("/batch")
def batch_start(body: dict):
    """Upload berurutan (satu per satu) dengan jadwal masing-masing."""
    import threading
    from datetime import datetime, timedelta, timezone
    from core.youtube_upload import UploadError, build_metadata
    from db.database import create_yt_upload
    from utils.constants import CHANNEL_TZ_OFFSET

    items = body.get("items") or []
    if not items or len(items) > BATCH_MAX:
        raise HTTPException(status_code=400, detail=f"Isi 1–{BATCH_MAX} video.")
    tz = timezone(timedelta(hours=CHANNEL_TZ_OFFSET))
    prepared, times = [], []
    # Validasi SEMUA item dulu; baru dicatat bila semuanya lolos (antrean tidak boleh tercatat setengah)
    for it in items:
        info = _render_info(it["render_id"])
        title, desc, tags, _ = _defaults_for(info)
        try:
            when = datetime.fromisoformat(it["publish_local"]).replace(tzinfo=tz) if it.get("publish_local") else None
            if when and any(abs((when - t).total_seconds()) < 60 * 60 for t in times):
                raise UploadError(f"Jadwal '{it.get('title') or title}' kurang dari 60 menit dari video lain di antrean.")
            meta = build_metadata(it.get("title") or title, desc, tags, when)
        except (UploadError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        if when:
            times.append(when)
        prepared.append((it["render_id"], info, meta))
    jobs = []
    for rid, info, meta in prepared:
        uid = create_yt_upload({"render_id": rid, "candidate_id": info.get("candidate_id"),
                                "file_path": info["file_path"], "title": meta["snippet"]["title"],
                                "description": meta["snippet"]["description"], "tags": meta["snippet"]["tags"],
                                "publish_at": meta["status"].get("publishAt")})
        jobs.append((uid, rid, info["file_path"], meta))

    def worker():
        for uid, rid, path, meta in jobs:
            _run_upload(uid, rid, path, meta)          # gagal satu tidak menghentikan sisanya

    threading.Thread(target=worker, daemon=True).start()
    return {"upload_ids": [j[0] for j in jobs]}
