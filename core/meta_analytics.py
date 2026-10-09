"""Analitik Instagram Reels dan Facebook Page lewat Meta Graph API.

Berbeda dari YouTube yang memakai OAuth loopback, di sini user MENEMPEL token
yang dibuatnya sendiri. Alasannya bukan malas: alur OAuth Meta menuntut redirect
URI ber-HTTPS publik, yang tidak dimiliki aplikasi yang jalan di localhost.
Token tempel menghindari seluruh masalah itu dan tetap aman selama tokennya
tidak keluar dari mesin ini.

Yang HARUS diketahui sebelum berharap banyak: Meta tidak menyediakan kurva
retensi per detik seperti YouTube. Yang ada hanya angka agregat — jangkauan,
jumlah putar, dan rata-rata waktu tonton. Jadi pertanyaan "penonton pergi di
detik ke berapa" tidak bisa dijawab di sini, hanya di YouTube.

Semua endpoint READ-ONLY. Tidak ada yang memposting atau mengubah apa pun.
"""

import logging

import requests

logger = logging.getLogger(__name__)

GRAPH_API = "https://graph.facebook.com/v21.0"

# Jalur kedua: "Instagram API with Instagram Login". Host dan token-nya berbeda
# dari Graph API Facebook, dan yang penting — TIDAK butuh Facebook Page. Akun
# profesional (Business maupun Creator) bisa dipakai langsung.
IG_API = "https://graph.instagram.com/v21.0"

REQUEST_TIMEOUT = 30

# Dua jenis koneksi yang didukung. Disimpan di DB supaya pemanggilan berikutnya
# tahu harus bicara ke host yang mana.
KIND_PAGE = "page"          # lewat Facebook Page yang menautkan akun IG
KIND_IG_LOGIN = "ig_login"  # langsung ke akun IG, tanpa Page


class MetaError(Exception):
    """Kegagalan yang sudah diterjemahkan jadi pesan yang bisa ditindaklanjuti."""


def _explain(payload: dict, status: int) -> str:
    error = payload.get("error", {}) if isinstance(payload, dict) else {}
    message = error.get("message", "")
    code = error.get("code")
    subcode = error.get("error_subcode")

    if code == 190:
        return (
            f"Token Meta tidak berlaku atau sudah kedaluwarsa ({message}). "
            f"Buat token baru di Graph API Explorer lalu tempel ulang di Settings."
        )
    if code in (10, 200) or subcode == 33:
        return (
            f"Izin kurang ({message}). Token butuh permission: pages_show_list, "
            f"pages_read_engagement, read_insights, instagram_basic, dan "
            f"instagram_manage_insights. `read_insights` paling sering terlewat "
            f"dan itulah yang dipakai metrik video Page."
        )
    if code == 4 or code == 17:
        return f"Kuota Meta API sementara habis ({message}). Coba lagi nanti."
    return f"Meta API error {status}: {message or payload}"


def _get(path: str, token: str, params: dict | None = None, base: str = GRAPH_API) -> dict:
    response = requests.get(
        f"{base}/{path.lstrip('/')}",
        params={**(params or {}), "access_token": token},
        timeout=REQUEST_TIMEOUT,
    )
    try:
        payload = response.json()
    except Exception:
        raise MetaError(f"Jawaban Meta tidak terbaca (HTTP {response.status_code}).")

    if response.status_code != 200 or "error" in payload:
        raise MetaError(_explain(payload, response.status_code))
    return payload


# ---------------------------------------------------------------- akun

def list_pages(token: str) -> list[dict]:
    """Facebook Page yang dikelola akun ini, plus akun IG yang tertaut.

    Instagram hanya bisa dianalisis lewat Page yang menautkannya — akun IG
    pribadi tanpa Page tidak punya jalur API sama sekali.
    """
    data = _get(
        "me/accounts",
        token,
        {
            "fields": (
                "id,name,access_token,followers_count,"
                "instagram_business_account{id,username,followers_count}"
            ),
            "limit": 50,
        },
    )

    pages = []
    for item in data.get("data", []):
        instagram = item.get("instagram_business_account") or {}
        pages.append({
            "page_id": item["id"],
            "page_name": item.get("name", ""),
            "page_token": item.get("access_token"),
            "page_followers": item.get("followers_count"),
            "ig_id": instagram.get("id"),
            "ig_username": instagram.get("username"),
            "ig_followers": instagram.get("followers_count"),
        })
    return pages


def diagnose_token(token: str) -> dict:
    """Periksa token apa adanya: milik siapa, izin apa, Page apa.

    Dibuat karena menebak penyebab "tidak ada Page" berulang kali tidak
    menyelesaikan apa pun. Tiga pertanyaan dipisah supaya jelas mana yang
    gagal: identitas akun, izin yang BENAR-BENAR diberikan, dan daftar Page.
    """
    report = {"identity": None, "granted": [], "declined": [], "pages": [], "errors": {}}

    try:
        report["identity"] = _get("me", token, {"fields": "id,name"})
    except MetaError as e:
        report["errors"]["identity"] = str(e)

    try:
        data = _get("me/permissions", token)
        for item in data.get("data", []):
            target = "granted" if item.get("status") == "granted" else "declined"
            report[target].append(item.get("permission"))
    except MetaError as e:
        report["errors"]["permissions"] = str(e)

    try:
        data = _get("me/accounts", token, {"fields": "id,name", "limit": 50})
        report["pages"] = data.get("data", [])
    except MetaError as e:
        report["errors"]["pages"] = str(e)

    return report


def probe_instagram_login(token: str) -> dict | None:
    """Coba baca akun IG lewat jalur Instagram Login. None kalau bukan itu jenisnya.

    Dipakai sebagai cadangan saat token tidak mengelola Facebook Page mana pun —
    kasus yang wajar untuk akun Creator yang tidak menautkan Page.
    """
    try:
        data = _get(
            "me",
            token,
            {"fields": "id,username,account_type,media_count,followers_count"},
            base=IG_API,
        )
    except MetaError as e:
        logger.info("Bukan token Instagram Login: %s", e)
        return None

    if not data.get("id"):
        return None

    return {
        "kind": KIND_IG_LOGIN,
        "ig_id": data["id"],
        "ig_username": data.get("username"),
        "account_type": data.get("account_type"),
        "ig_followers": data.get("followers_count"),
        "media_count": data.get("media_count"),
    }


def exchange_long_lived_ig(token: str, app_secret: str) -> dict:
    """Tukar token Instagram Login pendek jadi ~60 hari.

    Endpoint-nya berbeda dari penukaran token Facebook: `ig_exchange_token`, dan
    hanya butuh app secret — bukan app id.
    """
    data = _get(
        "access_token",
        token,
        {"grant_type": "ig_exchange_token", "client_secret": app_secret},
        base=IG_API,
    )
    return {
        "token": data.get("access_token"),
        "expires_in": data.get("expires_in"),
    }


def exchange_long_lived(token: str, app_id: str, app_secret: str) -> dict:
    """Tukar token pendek (1 jam) jadi token panjang (~60 hari).

    Token dari Graph API Explorer umumnya hanya berumur satu jam. Tanpa
    penukaran ini, koneksinya mati sebelum sempat dipakai besok.
    """
    data = _get(
        "oauth/access_token",
        token,
        {
            "grant_type": "fb_exchange_token",
            "client_id": app_id,
            "client_secret": app_secret,
            "fb_exchange_token": token,
        },
    )
    return {
        "token": data.get("access_token"),
        "expires_in": data.get("expires_in"),
    }


# ------------------------------------------------------------ instagram

# Metrik reel yang tersedia di Graph API. Tidak ada kurva retensi di sini —
# Meta tidak menyediakannya, jadi jangan berharap padanan `audienceWatchRatio`.
IG_REEL_METRICS = "reach,likes,comments,shares,saved,views,total_interactions"


def list_instagram_media(
    token: str, ig_id: str, limit: int = 50, kind: str = KIND_PAGE
) -> list[dict]:
    """Reels dan video terbaru sebuah akun IG profesional.

    Pada jalur Instagram Login, akun sendiri dirujuk sebagai `me` dan host-nya
    graph.instagram.com — bukan ID akun di graph.facebook.com.
    """
    base = IG_API if kind == KIND_IG_LOGIN else GRAPH_API
    path = "me/media" if kind == KIND_IG_LOGIN else f"{ig_id}/media"

    data = _get(
        path,
        token,
        {
            "fields": (
                "id,caption,media_type,media_product_type,permalink,"
                "timestamp,like_count,comments_count"
            ),
            "limit": limit,
        },
        base=base,
    )

    media = []
    for item in data.get("data", []):
        if item.get("media_type") not in ("VIDEO", "REELS"):
            continue
        media.append({
            "id": item["id"],
            "caption": (item.get("caption") or "")[:200],
            "permalink": item.get("permalink"),
            "timestamp": item.get("timestamp"),
            "product_type": item.get("media_product_type"),
            "likes": item.get("like_count"),
            "comments": item.get("comments_count"),
        })
    return media


def fetch_instagram_insights(
    token: str, media_id: str, kind: str = KIND_PAGE
) -> dict:
    """Insight satu media. Metrik yang ditolak dilewati, bukan menggagalkan semua.

    Daftar metrik yang sah berbeda-beda menurut jenis media dan berubah antar
    versi API. Meminta satu metrik yang tidak berlaku akan menggagalkan seluruh
    permintaan, jadi kegagalan di sini sengaja diperlakukan sebagai "tidak ada
    data" ketimbang error.
    """
    base = IG_API if kind == KIND_IG_LOGIN else GRAPH_API

    try:
        data = _get(f"{media_id}/insights", token, {"metric": IG_REEL_METRICS}, base)
    except MetaError as e:
        # Satu metrik yang tidak berlaku menggagalkan SELURUH permintaan, jadi
        # kegagalan pertama tidak berarti media ini tanpa data. Coba lagi dengan
        # kumpulan inti yang berlaku untuk hampir semua jenis media.
        logger.info("Insight lengkap gagal untuk %s: %s", media_id, e)
        try:
            data = _get(f"{media_id}/insights", token, {"metric": "reach"}, base)
        except MetaError as fallback_error:
            # Alasannya DIKEMBALIKAN, bukan ditelan. Metrik kosong tanpa
            # penjelasan membuat user mengira datanya memang tidak ada.
            return {"insight_error": str(fallback_error)}

    result = {}
    for item in data.get("data", []):
        values = item.get("values") or []
        if values:
            result[item["name"]] = values[0].get("value")
    return result


def fetch_instagram_audience(
    token: str, ig_id: str, kind: str = KIND_PAGE
) -> dict:
    """Demografi pengikut IG. Butuh jumlah pengikut minimum dari pihak Meta."""
    try:
        data = _get(
            "me/insights" if kind == KIND_IG_LOGIN else f"{ig_id}/insights",
            token,
            {
                "metric": "follower_demographics",
                "period": "lifetime",
                "metric_type": "total_value",
                "breakdown": "age,gender",
            },
            base=IG_API if kind == KIND_IG_LOGIN else GRAPH_API,
        )
    except MetaError as e:
        logger.info("Demografi IG tidak tersedia: %s", e)
        return {}

    breakdown = {}
    for item in data.get("data", []):
        total = item.get("total_value") or {}
        for group in (total.get("breakdowns") or []):
            for result in group.get("results", []):
                key = " ".join(result.get("dimension_values", []))
                breakdown[key] = result.get("value")
    return breakdown


# --------------------------------------------------------------- facebook

def list_page_videos(page_token: str, page_id: str, limit: int = 25) -> list[dict]:
    """Video di Facebook Page beserta metrik dasarnya."""
    data = _get(
        f"{page_id}/videos",
        page_token,
        {
            "fields": (
                "id,title,description,created_time,length,permalink_url,"
                "video_insights.metric(total_video_views,"
                "total_video_impressions,total_video_avg_time_watched)"
            ),
            "limit": limit,
        },
    )

    videos = []
    for item in data.get("data", []):
        insights = {}
        for entry in (item.get("video_insights", {}).get("data") or []):
            values = entry.get("values") or []
            if values:
                insights[entry["name"]] = values[0].get("value")

        videos.append({
            "id": item["id"],
            "title": item.get("title") or (item.get("description") or "")[:80],
            "created_time": item.get("created_time"),
            "length_seconds": item.get("length"),
            "permalink": item.get("permalink_url"),
            "views": insights.get("total_video_views"),
            "impressions": insights.get("total_video_impressions"),
            # Meta memberi angka ini dalam MILIDETIK, bukan detik.
            "avg_watch_seconds": (
                insights["total_video_avg_time_watched"] / 1000
                if insights.get("total_video_avg_time_watched") else None
            ),
        })
    return videos


def fetch_snapshot(token: str, page: dict, limit: int = 50) -> dict:
    """Kumpulkan semua yang bisa diambil, sesuai jalur koneksinya.

    Jalur Instagram Login tidak punya Facebook Page sama sekali, jadi bagian
    video Page dilewati — bukan gagal, memang tidak ada.
    """
    kind = page.get("kind") or KIND_PAGE
    snapshot = {
        "page_name": page.get("page_name"),
        "page_followers": page.get("page_followers"),
        "ig_username": page.get("ig_username"),
        "ig_followers": page.get("ig_followers"),
        "instagram": [],
        "facebook": [],
        "ig_audience": {},
    }

    if page.get("ig_id"):
        media = list_instagram_media(token, page["ig_id"], limit=limit, kind=kind)
        for item in media:
            item.update(fetch_instagram_insights(token, item["id"], kind=kind))
        snapshot["instagram"] = media
        snapshot["ig_audience"] = fetch_instagram_audience(
            token, page["ig_id"], kind=kind
        )

    # Facebook Page bisa datang dari dua arah: ikut token Page (jalur B), atau
    # dari koneksi terpisah yang ditambahkan di atas koneksi Instagram Login.
    fb_token = page.get("fb_token") or (
        page.get("page_token") or token if kind == KIND_PAGE else None
    )
    fb_page_id = page.get("fb_page_id") or (
        page["page_id"] if kind == KIND_PAGE else None
    )

    if fb_token and fb_page_id:
        try:
            snapshot["facebook"] = list_page_videos(fb_token, fb_page_id, limit=25)
        except MetaError as e:
            logger.info("Video Page dilewati: %s", e)
            snapshot["facebook_error"] = str(e)

    return snapshot
