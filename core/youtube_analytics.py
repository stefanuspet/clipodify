"""Koneksi ke akun YouTube sendiri: OAuth, daftar video, dan kurva retensi.

Dipakai untuk menjawab satu pertanyaan yang tidak bisa dijawab prompt maupun
sinyal prefilter: dari clip yang benar-benar diupload, mana yang ditonton sampai
habis. Semuanya READ-ONLY — tidak ada endpoint di sini yang menulis apa pun ke
akun user.

Dua API yang dipakai:
- Data API v3        → identitas channel, daftar video, durasi
- Analytics API v2   → views, average view duration, dan kurva retensi

Kurva retensi hanya ada di Analytics API dan hanya untuk channel milik sendiri.
Itulah alasan modul ini butuh OAuth, bukan sekadar API key.
"""

import json
import logging
import re

import requests
from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

from db.database import get_yt_auth, save_yt_auth, update_yt_token

logger = logging.getLogger(__name__)

# yt-analytics.readonly memberi metrik; youtube.readonly memberi daftar video dan
# judulnya. youtube.upload (Fase 9) HANYA bisa mengunggah video baru — tidak bisa
# mengubah/menghapus video lain atau pengaturan channel. Koneksi lama tanpa scope ini
# perlu dihubungkan ulang sekali.
SCOPES = [
    "https://www.googleapis.com/auth/yt-analytics.readonly",
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/youtube.upload",
]

DATA_API = "https://www.googleapis.com/youtube/v3"
ANALYTICS_API = "https://youtubeanalytics.googleapis.com/v2/reports"

REQUEST_TIMEOUT = 30

# Tanggal minimal yang didukung oleh YouTube Analytics API v2 adalah 1 September 2010.
START_DATE = "2010-09-01"


class NotConnectedError(Exception):
    """Belum ada koneksi YouTube yang tersimpan."""


# ------------------------------------------------------------------ auth

def connect(client_secrets_path: str, port: int = 0) -> dict:
    """Jalankan OAuth dan simpan hasilnya. Return info channel.

    `run_local_server` membuka browser dan menunggu callback di localhost. Itu
    memblokir sampai user selesai menyetujui — di Streamlit halaman akan tampak
    berputar selama itu, dan memang begitu adanya.

    port=0 berarti sistem yang memilih port bebas. Kalau OAuth client-mu mendaftar
    redirect URI dengan port tetap, isi port itu di sini.
    """
    with open(client_secrets_path, "r", encoding="utf-8") as f:
        client_secrets = f.read()

    flow = InstalledAppFlow.from_client_secrets_file(client_secrets_path, SCOPES)
    creds = flow.run_local_server(port=port, prompt="consent")

    channel = _fetch_channel(creds)
    save_yt_auth(
        client_secrets,
        creds.to_json(),
        channel["channel_id"],
        channel["channel_title"],
    )
    return channel


def load_credentials() -> Credentials:
    """Kredensial siap pakai, di-refresh otomatis kalau sudah kedaluwarsa."""
    auth = get_yt_auth()
    if not auth:
        raise NotConnectedError(
            "Belum terhubung ke YouTube. Hubungkan dulu di halaman Analitik."
        )

    # Memuat kredensial dari JSON string simpanan
    token_data = json.loads(auth["token_json"])
    creds = Credentials.from_authorized_user_info(token_data)

    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except RefreshError as e:
            # Penyebab tersering BUKAN kesalahan pemakaian: Google mencabut
            # refresh token setiap 7 hari selama OAuth consent screen masih
            # berstatus Testing. Tanpa pesan ini, user cuma melihat error mentah
            # dan mengira datanya rusak.
            raise NotConnectedError(
                "Koneksi YouTube kedaluwarsa dan tidak bisa diperbarui otomatis "
                f"({e}). Kalau OAuth consent screen-mu masih berstatus Testing, "
                "Google mencabut izin tiap 7 hari — hubungkan ulang, atau ubah "
                "status app-nya jadi In production supaya tidak berulang."
            ) from e
        # Token hasil refresh WAJIB disimpan. Tanpa ini, tiap sesi baru
        # mengulang refresh yang sama dan access token lama menumpuk sia-sia.
        update_yt_token(creds.to_json())

    return creds


def is_connected() -> bool:
    return get_yt_auth() is not None


# ------------------------------------------------------------------ HTTP

def _get(url: str, creds: Credentials, params: dict) -> dict:
    response = requests.get(
        url,
        params=params,
        headers={"Authorization": f"Bearer {creds.token}"},
        timeout=REQUEST_TIMEOUT,
    )
    if response.status_code != 200:
        raise Exception(_explain_error(response))
    return response.json()


def _explain_error(response: requests.Response) -> str:
    """Pesan error yang menyebut penyebab paling mungkin, bukan cuma kode HTTP."""
    try:
        detail = response.json().get("error", {}).get("message", "")
    except Exception:
        detail = response.text[:200]

    if response.status_code == 403:
        return (
            f"Ditolak YouTube (403): {detail}. Penyebab tersering: API-nya belum "
            f"diaktifkan di Google Cloud Console, atau akun yang login bukan "
            f"pemilik channel."
        )
    if response.status_code == 401:
        return (
            f"Token tidak berlaku (401): {detail}. Putuskan koneksi lalu hubungkan "
            f"ulang."
        )
    return f"YouTube API error {response.status_code}: {detail}"


# ------------------------------------------------------------- channel

def _fetch_channel(creds: Credentials) -> dict:
    data = _get(
        f"{DATA_API}/channels",
        creds,
        {"part": "snippet,contentDetails,statistics", "mine": "true"},
    )
    items = data.get("items") or []
    if not items:
        raise Exception(
            "Akun ini tidak punya channel YouTube. Login dengan akun pemilik channel."
        )

    item = items[0]
    stats = item.get("statistics", {})
    return {
        "channel_id": item["id"],
        "channel_title": item["snippet"]["title"],
        "uploads_playlist": item["contentDetails"]["relatedPlaylists"]["uploads"],
        "subscribers": int(stats.get("subscriberCount", 0) or 0),
        "total_views": int(stats.get("viewCount", 0) or 0),
        "video_count": int(stats.get("videoCount", 0) or 0),
    }


def get_channel(creds: Credentials | None = None) -> dict:
    return _fetch_channel(creds or load_credentials())


# --------------------------------------------------------------- videos

def _parse_iso_duration(value: str) -> float:
    """'PT1M35S' -> 95.0, 'P1DT2H3M4S' -> 93784.0. ISO 8601 Duration Parser."""
    if not value or not isinstance(value, str):
        return 0.0

    pattern = re.compile(
        r"P(?:(?P<days>\d+)D)?"
        r"(?:T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+(?:\.\d+)?)S)?)?"
    )
    match = pattern.match(value)
    if not match:
        return 0.0

    parts = match.groupdict()
    days = float(parts["days"] or 0)
    hours = float(parts["hours"] or 0)
    minutes = float(parts["minutes"] or 0)
    seconds = float(parts["seconds"] or 0)

    return (days * 86400) + (hours * 3600) + (minutes * 60) + seconds


def list_videos(creds: Credentials, uploads_playlist: str, limit: int = 50) -> list[dict]:
    """Video terbaru dari playlist uploads, lengkap dengan durasinya."""
    video_ids, page_token = [], None

    while len(video_ids) < limit:
        params = {
            "part": "contentDetails",
            "playlistId": uploads_playlist,
            "maxResults": min(50, limit - len(video_ids)),
        }
        if page_token:
            params["pageToken"] = page_token

        data = _get(f"{DATA_API}/playlistItems", creds, params)
        video_ids += [
            item["contentDetails"]["videoId"] for item in data.get("items", [])
        ]

        page_token = data.get("nextPageToken")
        if not page_token:
            break

    if not video_ids:
        return []

    videos = []
    # Data API menerima maksimal 50 id per panggilan.
    for chunk_start in range(0, len(video_ids), 50):
        chunk = video_ids[chunk_start:chunk_start + 50]
        data = _get(
            f"{DATA_API}/videos",
            creds,
            {"part": "snippet,contentDetails,statistics", "id": ",".join(chunk)},
        )
        for item in data.get("items", []):
            videos.append({
                "video_id": item["id"],
                "title": item["snippet"]["title"],
                "published_at": item["snippet"]["publishedAt"],
                "duration_seconds": _parse_iso_duration(
                    item["contentDetails"].get("duration", "")
                ),
                "views": int(item.get("statistics", {}).get("viewCount", 0) or 0),
            })

    return videos


# ------------------------------------------------------------ analytics

def _today() -> str:
    from datetime import date
    return date.today().isoformat()


def fetch_video_metrics(creds: Credentials, video_ids: list[str]) -> dict[str, dict]:
    """Metrik per video: views, rata-rata durasi tonton, dan persentasenya.

    Analytics API membatasi panjang filter, jadi id dikirim per rombongan.
    """
    if not video_ids:
        return {}

    metrics: dict[str, dict] = {}

    for chunk_start in range(0, len(video_ids), 50):
        chunk = video_ids[chunk_start:chunk_start + 50]
        data = _get(
            ANALYTICS_API,
            creds,
            {
                "ids": "channel==MINE",
                "startDate": START_DATE,
                "endDate": _today(),
                "metrics": (
                    "views,averageViewDuration,averageViewPercentage,"
                    "likes,comments,shares,subscribersGained,estimatedMinutesWatched"
                ),
                "dimensions": "video",
                "filters": f"video=={','.join(chunk)}",
                "maxResults": 200,
            },
        )
        columns = [c["name"] for c in data.get("columnHeaders", [])]
        mapping = {
            "views": "views",
            "averageViewDuration": "avg_view_seconds",
            "averageViewPercentage": "avg_view_percentage",
            "likes": "likes",
            "comments": "comments",
            "shares": "shares",
            "subscribersGained": "subs_gained",
            "estimatedMinutesWatched": "minutes_watched",
        }
        for row in data.get("rows", []):
            record = dict(zip(columns, row))
            video_id = record.get("video")
            if not video_id:
                continue
            metrics[video_id] = {
                key: record[column]
                for column, key in mapping.items()
                if column in record
            }

    return metrics


def fetch_traffic_sources(creds: Credentials) -> list[dict]:
    """Dari mana view datang. Ini yang menentukan saran setup mana yang relevan."""
    data = _get(
        ANALYTICS_API,
        creds,
        {
            "ids": "channel==MINE",
            "startDate": START_DATE,
            "endDate": _today(),
            "metrics": "views",
            "dimensions": "insightTrafficSourceType",
            "sort": "-views",
        },
    )
    return [{"source": row[0], "views": row[1]} for row in data.get("rows", [])]


def _safe_report(creds: Credentials, params: dict) -> list[list]:
    """Jalankan satu laporan; kembalikan daftar kosong kalau ditolak."""
    try:
        return _get(ANALYTICS_API, creds, params).get("rows", []) or []
    except Exception as e:
        logger.warning("Laporan dilewati (%s): %s", params.get("dimensions"), e)
        return []


def fetch_audience(creds: Credentials) -> dict:
    """Siapa yang menonton: umur, gender, negara, dan perangkat."""
    base = {
        "ids": "channel==MINE",
        "startDate": START_DATE,
        "endDate": _today(),
    }

    demographics = _safe_report(creds, {
        **base, "dimensions": "ageGroup,gender", "metrics": "viewerPercentage",
    })
    countries = _safe_report(creds, {
        **base, "dimensions": "country",
        "metrics": "views,averageViewPercentage", "sort": "-views", "maxResults": 10,
    })
    devices = _safe_report(creds, {
        **base, "dimensions": "deviceType", "metrics": "views", "sort": "-views",
    })
    locations = _safe_report(creds, {
        **base, "dimensions": "insightPlaybackLocationType",
        "metrics": "views", "sort": "-views",
    })

    return {
        "demographics": [
            {"age": row[0], "gender": row[1], "percentage": row[2]}
            for row in demographics
        ],
        "countries": [
            {"country": row[0], "views": row[1], "retention": row[2]}
            for row in countries
        ],
        "devices": [{"device": row[0], "views": row[1]} for row in devices],
        "locations": [{"location": row[0], "views": row[1]} for row in locations],
    }


def fetch_engagement(creds: Credentials) -> dict:
    """Like, komentar, share, dan konversi subscriber untuk seluruh channel."""
    data = _get(
        ANALYTICS_API,
        creds,
        {
            "ids": "channel==MINE",
            "startDate": START_DATE,
            "endDate": _today(),
            "metrics": "views,likes,comments,shares,subscribersGained,subscribersLost",
        },
    )
    rows = data.get("rows") or [[0, 0, 0, 0, 0, 0]]
    keys = ["views", "likes", "comments", "shares", "subs_gained", "subs_lost"]
    return dict(zip(keys, rows[0]))


def fetch_video_metadata(creds: Credentials, video_ids: list[str]) -> dict[str, dict]:
    """Judul, deskripsi, tag, dan kategori tiap video — bahan saran setup."""
    if not video_ids:
        return {}

    metadata: dict[str, dict] = {}

    for chunk_start in range(0, len(video_ids), 50):
        chunk = video_ids[chunk_start:chunk_start + 50]
        data = _get(
            f"{DATA_API}/videos",
            creds,
            {"part": "snippet", "id": ",".join(chunk)},
        )
        for item in data.get("items", []):
            snippet = item["snippet"]
            metadata[item["id"]] = {
                "title": snippet.get("title", ""),
                "description": snippet.get("description", "") or "",
                "tags": snippet.get("tags") or [],
                "category_id": snippet.get("categoryId"),
                "published_at": snippet.get("publishedAt"),
            }

    return metadata


def fetch_branding(creds: Credentials) -> dict:
    """Identitas channel: deskripsi, keyword, negara, handle."""
    data = _get(
        f"{DATA_API}/channels",
        creds,
        {"part": "snippet,brandingSettings,statistics", "mine": "true"},
    )
    items = data.get("items") or []
    if not items:
        raise Exception("Channel tidak ditemukan.")

    item = items[0]
    snippet = item["snippet"]
    branding = item.get("brandingSettings", {}).get("channel", {})
    stats = item.get("statistics", {})

    return {
        "title": snippet.get("title", ""),
        "description": snippet.get("description", "") or "",
        "keywords": branding.get("keywords", "") or "",
        "country": snippet.get("country"),
        "custom_url": snippet.get("customUrl"),
        "subscribers": int(stats.get("subscriberCount", 0) or 0),
        "total_views": int(stats.get("viewCount", 0) or 0),
        "video_count": int(stats.get("videoCount", 0) or 0),
    }


def fetch_setup_snapshot(creds: Credentials, limit: int = 50) -> dict:
    """Semua bahan saran setup channel dalam satu panggilan."""
    branding = fetch_branding(creds)
    channel = get_channel(creds)
    videos = list_videos(creds, channel["uploads_playlist"], limit=limit)
    video_ids = [v["video_id"] for v in videos]
    metadata = fetch_video_metadata(creds, video_ids)
    metrics = fetch_video_metrics(creds, video_ids)

    for video in videos:
        video.update(metadata.get(video["video_id"], {}))
        video.update(metrics.get(video["video_id"], {}))

    return {
        "branding": branding,
        "videos": videos,
        "traffic": fetch_traffic_sources(creds),
        "engagement": fetch_engagement(creds),
        "audience": fetch_audience(creds),
    }


def fetch_retention_curve(creds: Credentials, video_id: str) -> list[dict]:
    """Kurva retensi satu video."""
    if not video_id:
        return []

    data = _get(
        ANALYTICS_API,
        creds,
        {
            "ids": "channel==MINE",
            "startDate": START_DATE,
            "endDate": _today(),
            "metrics": "audienceWatchRatio,relativeRetentionPerformance",
            "dimensions": "elapsedVideoTimeRatio",
            "filters": f"video=={video_id}",
        },
    )

    curve = []
    for row in data.get("rows", []):
        curve.append({
            "ratio": row[0],
            "watch_ratio": row[1],
            "relative": row[2] if len(row) > 2 else None,
        })
    curve.sort(key=lambda point: point["ratio"])
    return curve