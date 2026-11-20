"""Router: podcast scout — channel pantau dan scan episode terbaru."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from backend.schemas import AddChannelRequest, RecommendRequest, ScanRequest

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/scout", tags=["scout"])


def _require_yt():
    from core.youtube_analytics import is_connected
    if not is_connected():
        raise HTTPException(status_code=401, detail="Belum terhubung ke YouTube.")


# ---------------------------------------------------------------- channels

@router.get("/channels")
def list_channels():
    from db.database import get_watch_channels
    return get_watch_channels()


@router.post("/channels")
def add_channel(body: AddChannelRequest):
    _require_yt()
    from core import podcast_scout
    from core.youtube_analytics import load_credentials
    from db.database import add_watch_channel

    try:
        creds = load_credentials()
        found = podcast_scout.resolve_channel(creds, body.handle)
        add_watch_channel(
            found["channel_id"],
            found["handle"],
            found["title"],
            found["subscribers"],
        )
        return found
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.delete("/channels/{channel_id}")
def delete_channel(channel_id: str):
    from db.database import delete_watch_channel, get_watch_channels
    channels = get_watch_channels()
    if not any(c["channel_id"] == channel_id for c in channels):
        raise HTTPException(status_code=404, detail="Channel tidak ditemukan.")
    delete_watch_channel(channel_id)
    return {"ok": True}


# ---------------------------------------------------------------- scan

@router.post("/scan")
def scan(body: ScanRequest):
    _require_yt()
    from core import podcast_scout
    from core.youtube_analytics import load_credentials
    from db.database import get_watch_channels

    channels = get_watch_channels()
    if not channels:
        raise HTTPException(status_code=422, detail="Belum ada channel dipantau.")

    try:
        creds = load_credentials()
        episodes = podcast_scout.fetch_recent_episodes(
            creds,
            channels,
            max_age_hours=body.max_age_hours,
            min_minutes=body.min_minutes,
        )
        for ep in episodes:
            ep["source"] = "dipantau"

        if body.wide and body.queries:
            discovered = podcast_scout.discover_episodes(
                creds,
                body.queries,
                max_age_hours=body.max_age_hours,
                min_minutes=body.min_minutes,
            )
            seen = {ep["video_id"] for ep in episodes}
            for ep in discovered:
                if ep["video_id"] not in seen:
                    ep["source"] = "temuan"
                    episodes.append(ep)

        episodes = podcast_scout.sort_episodes(episodes, body.sort_mode)
        return {"episodes": episodes, "count": len(episodes)}

    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/sort-modes")
def get_sort_modes():
    from core.podcast_scout import SORT_MODES
    return list(SORT_MODES.keys())


# ---------------------------------------------------------------- recommend

@router.post("/recommend")
def recommend(body: RecommendRequest):
    _require_yt()
    from core import podcast_scout
    from core.youtube_analytics import fetch_audience, is_connected, load_credentials
    from db.database import get_yt_videos

    if not body.episodes:
        raise HTTPException(status_code=400, detail="episodes wajib diisi.")

    audience = None
    try:
        creds = load_credentials()
        audience = fetch_audience(creds)
    except Exception:
        pass

    try:
        text = podcast_scout.recommend(body.episodes, get_yt_videos(), audience)
        return {"recommendation": text}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
