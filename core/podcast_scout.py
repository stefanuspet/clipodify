"""Cari episode podcast yang layak diklip, dari channel yang dipantau sendiri.

Kenapa daftar pantauan, bukan pencarian YouTube: pencarian `podcast indonesia`
mengembalikan channel kecil yang tidak relevan — diuji langsung, hasil teratasnya
channel dengan penonton segelintir. Selain itu `search.list` berharga 100 unit
kuota sekali jalan dari jatah 10.000 per hari, sementara memantau satu channel
lewat playlist uploads cuma ~2 unit. Daftar yang dikurasi jauh lebih tajam DAN
jauh lebih murah.

Angka yang menentukan bukan jumlah view, tapi VIEW PER JAM. Episode dengan 1,5
juta view dalam 44 jam sudah dipanen ratusan clipper; episode 500 ribu view dalam
21 jam masih terbuka. Jumlah view mentah tidak bisa membedakan keduanya.
"""

import logging
import re
from datetime import datetime, timedelta, timezone

from google.genai import types

from core import gemini_client
from core.youtube_analytics import DATA_API, _get, _parse_iso_duration

logger = logging.getLogger(__name__)

# Episode podcast, bukan Shorts. Di bawah ini hampir pasti potongan promosi yang
# sudah jadi clip — tidak ada gunanya diklip ulang.
MIN_EPISODE_MINUTES = 15


def _extract_handle(value: str) -> tuple[str, str]:
    """Ubah masukan user jadi (jenis, nilai) untuk parameter API.

    Menerima @handle, URL channel, URL /c/ atau /user/ lama, dan channel ID
    mentah. Yang terakhir dikenali dari pola UC + 22 karakter.
    """
    value = (value or "").strip().strip("/")

    match = re.search(r"youtube\.com/(@[\w.\-]+)", value)
    if match:
        return "forHandle", match.group(1).lstrip("@")

    match = re.search(r"youtube\.com/channel/(UC[\w\-]{22})", value)
    if match:
        return "id", match.group(1)

    match = re.search(r"youtube\.com/(?:c|user)/([\w.\-]+)", value)
    if match:
        return "forHandle", match.group(1)

    if re.fullmatch(r"UC[\w\-]{22}", value):
        return "id", value

    return "forHandle", value.lstrip("@")


def resolve_channel(creds, value: str) -> dict:
    """Cari satu channel dari handle, URL, atau ID."""
    param, resolved = _extract_handle(value)
    data = _get(
        f"{DATA_API}/channels",
        creds,
        {"part": "snippet,contentDetails,statistics", param: resolved},
    )

    items = data.get("items") or []
    if not items:
        raise ValueError(
            f"Channel '{value}' tidak ditemukan. Coba tempel URL channel-nya "
            f"langsung, atau handle yang diawali @."
        )

    item = items[0]
    return {
        "channel_id": item["id"],
        "handle": item["snippet"].get("customUrl") or resolved,
        "title": item["snippet"]["title"],
        "subscribers": int(item["statistics"].get("subscriberCount", 0) or 0),
        "uploads_playlist": item["contentDetails"]["relatedPlaylists"]["uploads"],
    }


def _hours_since(published_at: str) -> float:
    moment = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
    hours = (datetime.now(timezone.utc) - moment).total_seconds() / 3600
    return max(hours, 0.5)  # jangan bagi nol untuk video yang baru saja tayang


def fetch_recent_episodes(
    creds,
    channels: list[dict],
    max_age_hours: int = 72,
    min_minutes: int = MIN_EPISODE_MINUTES,
    per_channel: int = 6,
) -> list[dict]:
    """Episode terbaru dari tiap channel yang dipantau, lengkap dengan velocity.

    `max_age_hours` menentukan seberapa segar bahannya. Lewat 3 hari, episode
    populer sudah diklip banyak orang dan peluangnya jauh lebih tipis.
    """
    episodes: list[dict] = []

    for channel in channels:
        uploads = channel.get("uploads_playlist")
        if not uploads:
            try:
                uploads = resolve_channel(creds, channel["channel_id"])["uploads_playlist"]
            except Exception as e:
                logger.warning("Channel %s dilewati: %s", channel.get("title"), e)
                continue

        try:
            playlist = _get(
                f"{DATA_API}/playlistItems",
                creds,
                {"part": "contentDetails", "playlistId": uploads,
                 "maxResults": per_channel},
            )
            ids = [
                item["contentDetails"]["videoId"]
                for item in playlist.get("items", [])
            ]
            if not ids:
                continue

            data = _get(
                f"{DATA_API}/videos",
                creds,
                {"part": "snippet,statistics,contentDetails", "id": ",".join(ids)},
            )
        except Exception as e:
            logger.warning("Gagal membaca channel %s: %s", channel.get("title"), e)
            continue

        for item in data.get("items", []):
            # Channel di daftar pantauan dipercaya, jadi saringan kategori dan
            # batas durasi atas TIDAK diberlakukan di sini — hanya umur tayang,
            # durasi minimum, dan bukan siaran langsung.
            snippet = item["snippet"]
            if snippet.get("liveBroadcastContent") not in (None, "none"):
                continue

            duration = _parse_iso_duration(item["contentDetails"].get("duration", ""))
            if duration < min_minutes * 60:
                continue
            if _hours_since(snippet["publishedAt"]) > max_age_hours:
                continue

            episodes.append(_to_episode(item, duration))

    episodes.sort(key=lambda e: -e["velocity"])
    return episodes


# Kategori YouTube tempat podcast berada. 22 People & Blogs, 24 Entertainment,
# 25 News & Politics. Gaming (20) dan Music (10) sengaja tidak masuk — chart
# trending Indonesia dikuasai keduanya, dan tidak satu pun bisa diklip.
TALK_CATEGORIES = {"22", "24", "25"}

# Batas atas durasi. Tanpa ini, livestream gaming 700 menit ikut lolos saringan
# "durasi panjang" dan mendominasi hasil.
MAX_EPISODE_MINUTES = 180

SEARCH_COST_UNITS = 100


def _passes_episode_filter(item: dict, min_minutes: int, max_age_hours: int) -> float | None:
    """Kembalikan durasi detik kalau lolos, None kalau tidak.

    Menyaring empat hal sekaligus: siaran langsung, kategori bukan bicara,
    durasi di luar rentang episode, dan umur tayang.
    """
    snippet = item.get("snippet", {})
    if snippet.get("liveBroadcastContent") not in (None, "none"):
        return None
    if snippet.get("categoryId") and snippet["categoryId"] not in TALK_CATEGORIES:
        return None

    duration = _parse_iso_duration(item.get("contentDetails", {}).get("duration", ""))
    if not (min_minutes * 60 <= duration <= MAX_EPISODE_MINUTES * 60):
        return None

    if _hours_since(snippet["publishedAt"]) > max_age_hours:
        return None

    return duration


def _to_episode(item: dict, duration: float) -> dict:
    """Satu episode plus dua angka reaksi turunan.

    `velocity` menjawab "masih segar atau sudah dipanen clipper lain" — soal
    waktu. `comment_rate` menjawab pertanyaan yang berbeda: apakah isinya bikin
    penonton BERGERAK. Keduanya sengaja tidak dilebur jadi satu skor, karena
    episode bisa unggul di satu sumbu dan payah di sumbu lain, dan penggabungan
    menyembunyikan justru perbedaan yang perlu dilihat.

    `comments` bernilai None kalau kolom komentar DIMATIKAN, bukan 0. YouTube
    menghilangkan `commentCount` sama sekali dalam kasus itu, dan menyamakannya
    dengan nol akan membuat episode yang komentarnya ditutup terlihat seperti
    episode membosankan — dua hal yang sangat berbeda buat clipper.
    """
    snippet = item["snippet"]
    stats = item.get("statistics", {})
    views = int(stats.get("viewCount", 0) or 0)
    hours = _hours_since(snippet["publishedAt"])

    likes = int(stats.get("likeCount", 0) or 0)
    raw_comments = stats.get("commentCount")
    comments = int(raw_comments) if raw_comments is not None else None

    return {
        "video_id": item["id"],
        "url": f"https://www.youtube.com/watch?v={item['id']}",
        "title": snippet["title"],
        "channel": snippet["channelTitle"],
        "channel_id": snippet["channelId"],
        "published_at": snippet["publishedAt"],
        "hours_since": hours,
        "duration_seconds": duration,
        "views": views,
        "likes": likes,
        "comments": comments,
        "comments_disabled": raw_comments is None,
        "language": snippet.get("defaultAudioLanguage") or snippet.get("defaultLanguage"),
        "velocity": views / hours,
        "like_rate": (likes / views) if views else 0.0,
        # None, bukan 0.0, saat komentar dimatikan atau view masih nol — supaya
        # pengurutan bisa menaruhnya di bawah tanpa mengklaim episodenya sepi.
        "comment_rate": (comments / views) if (views and comments is not None) else None,
    }


# Cara mengurutkan hasil pindaian. Disediakan sebagai pilihan, BUKAN sebagai
# satu skor gabungan: "seberapa ramai" dan "seberapa memancing reaksi" adalah
# dua pertanyaan berbeda, dan meleburnya jadi satu angka menyembunyikan episode
# yang sepi tapi isinya bikin orang berdebat — justru bahan yang paling dicari.
SORT_MODES = {
    "view/jam (paling segar)": lambda e: -(e.get("velocity") or 0),
    "comment rate (paling memancing)": lambda e: -(e.get("comment_rate") or 0),
    "like rate": lambda e: -(e.get("like_rate") or 0),
    "total view": lambda e: -(e.get("views") or 0),
}

DEFAULT_SORT = "view/jam (paling segar)"


def sort_episodes(episodes: list[dict], mode: str = DEFAULT_SORT) -> list[dict]:
    """Urutkan episode sesuai mode. Mode tak dikenal jatuh ke bawaan."""
    key = SORT_MODES.get(mode, SORT_MODES[DEFAULT_SORT])
    return sorted(episodes, key=key)


def discover_episodes(
    creds,
    queries: list[str],
    max_age_hours: int = 72,
    min_minutes: int = MIN_EPISODE_MINUTES,
    region: str = "ID",
    language: str = "id",
    per_query: int = 25,
) -> list[dict]:
    """Cari episode dari SELURUH YouTube, bukan cuma channel yang dipantau.

    Diperingkat murni oleh view per jam — jumlah subscriber channel tidak ikut
    menentukan sama sekali. Channel 200 ribu subscriber yang episodenya jalan
    30.000 view/jam memang lebih layak dikerjakan daripada channel 25 juta
    subscriber yang episodenya jalan 5.000/jam.

    MAHAL: tiap query berharga ~100 unit dari jatah 10.000 unit per hari,
    sementara memantau satu channel cuma ~2 unit. Dipakai untuk MENEMUKAN
    channel baru, lalu channel yang bagus dipindahkan ke daftar pantauan.

    Catatan: `regionCode` dan `relevanceLanguage` hanya condong, bukan menyaring
    — hasil uji tetap membawa konten India dan Spanyol. Penyaringan bahasa yang
    sebenarnya dilakukan di sini dari `defaultAudioLanguage` tiap video.
    """
    published_after = (
        datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
    ).isoformat().replace("+00:00", "Z")

    found: dict[str, dict] = {}

    for query in queries:
        query = query.strip()
        if not query:
            continue
        try:
            results = _get(
                f"{DATA_API}/search",
                creds,
                {
                    "part": "snippet", "q": query, "type": "video",
                    "order": "viewCount", "publishedAfter": published_after,
                    "regionCode": region, "relevanceLanguage": language,
                    "videoDuration": "long", "maxResults": per_query,
                },
            )
        except Exception as e:
            logger.warning("Query %r gagal: %s", query, e)
            continue

        ids = [
            item["id"]["videoId"]
            for item in results.get("items", [])
            if item.get("id", {}).get("videoId")
        ]
        if not ids:
            continue

        details = _get(
            f"{DATA_API}/videos",
            creds,
            {"part": "snippet,statistics,contentDetails", "id": ",".join(ids)},
        )
        for item in details.get("items", []):
            duration = _passes_episode_filter(item, min_minutes, max_age_hours)
            if duration is None:
                continue
            episode = _to_episode(item, duration)
            # Bahasa hanya dibuang kalau video-nya MENYATAKAN bahasa lain.
            # Banyak video Indonesia tidak mengisi field ini sama sekali;
            # membuang yang kosong akan membuang justru sasaran utama.
            if episode["language"] and not episode["language"].startswith(language):
                continue
            found[episode["video_id"]] = episode

    episodes = sorted(found.values(), key=lambda e: -e["velocity"])
    return episodes


SYSTEM_PROMPT = """
Kamu penasihat seorang clipper: orang yang memotong podcast panjang jadi short
untuk TikTok, Reels, dan YouTube Shorts. Tugasmu memilih EPISODE MANA yang layak
dikerjakan sekarang, bukan memilih potongannya.

Kamu menilai DUA HAL YANG BERBEDA, dan keduanya harus disebut terpisah. Jangan
meleburnya jadi satu penilaian "bagus/tidak".

A. SEBERAPA SEGAR — apakah bahannya masih terbuka atau sudah dipanen.
- VIEW PER JAM jauh lebih penting daripada total view. Total view besar dengan
  umur tayang lama berarti episode itu sudah dipanen ratusan clipper — bahan
  bagusnya sudah beredar dan kamu datang terlambat.
- Episode yang masih muda dengan view per jam tinggi adalah peluang terbaik:
  ramai, tapi belum habis diklip.
- Episode yang sudah tua tapi view per jam-nya tetap tinggi juga menarik, itu
  tanda topiknya bertahan.

B. SEBERAPA MEMANCING REAKSI — apakah isinya bikin penonton BERGERAK.
Ini diukur dari COMMENT RATE (komentar dibagi view), bukan jumlah komentar
mentah. Angka mentah cuma mengukur besarnya channel.
- Comment rate tinggi berarti isi episodenya bikin orang berdebat, membantah,
  atau menyambung cerita. Itulah bahan mentah untuk clip yang memancing
  komentar — dan clip semacam itulah yang menyebar.
- Comment rate rendah dengan view sangat tinggi berarti orang menonton lalu
  diam saja. Episode seperti ini menghasilkan clip yang ditonton habis lalu
  berhenti di situ.
- LIKE RATE mengukur hal yang lebih lemah — orang suka, tapi tidak tergerak
  melakukan apa pun. Berguna, tapi jangan disamakan bobotnya dengan comment rate.

Kenapa B penting: masalah user BUKAN clip-nya kurang ditonton, melainkan
clip-nya tidak memancing reaksi. Memilih episode yang penontonnya memang aktif
bereaksi adalah cara memperbaikinya dari hulu.

DUA JEBAKAN saat membaca comment rate — sebut kalau relevan:
- Channel besar cenderung punya comment rate lebih rendah karena audiensnya
  terdilusi. Membandingkan lintas ukuran channel tidak sepenuhnya adil.
- Episode bertanda "komentar DIMATIKAN" TIDAK boleh dianggap sepi. Datanya
  memang tidak ada. Kalau menyarankannya, katakan bahwa sisi reaksinya tidak
  terbaca.

Episode terbaik unggul di KEDUA sumbu. Kalau harus memilih salah satu, katakan
mana yang kamu korbankan dan kenapa.

Kamu juga diberi DATA PERFORMA CHANNEL USER SENDIRI. Pakai itu. Rekomendasi
yang mengabaikan apa yang terbukti berhasil di channelnya tidak ada gunanya.
Kalau topik tertentu terbukti tidak jalan di channelnya, jangan sarankan episode
bertopik itu hanya karena episodenya ramai.

KEJUJURAN:
- Kamu hanya melihat judul dan angka, BUKAN isi episodenya. Jangan berlagak tahu
  ada momen bagus di dalamnya. Katakan apa yang membuatmu menduga, dan sebut itu
  dugaan.
- Kalau tidak ada episode yang benar-benar layak, katakan begitu. Jangan
  memaksakan rekomendasi supaya daftarnya terisi.
- Data performa channel user mungkin sedikit. Kalau begitu, jangan berlagak tahu
  polanya sudah pasti.

FORMAT — markdown, Bahasa Indonesia:

## Rekomendasi utama
2-4 episode, urut prioritas. Untuk tiap episode:
- Judul dan channel-nya
- Angka yang jadi alasan — WAJIB menyebut view per jam DAN comment rate, karena
  itu dua sumbu yang berbeda
- Kenapa cocok DENGAN channel user, merujuk data performanya
- Apa yang perlu dicari saat menonton episodenya — dugaan, bukan kepastian

## Yang sebaiknya dilewati
Episode yang ramai tapi tidak kamu sarankan, dan alasannya. Ini sama pentingnya
dengan rekomendasi.

## Catatan waktu
Seberapa mendesak. Episode mana yang akan basi kalau dikerjakan besok.

Jangan basa-basi.
"""


def _format_own_performance(own_videos: list[dict]) -> str:
    """Ringkas performa channel user, supaya rekomendasi menempel pada datanya."""
    if not own_videos:
        return "(belum ada data performa channel user)"

    lines = []
    for video in sorted(own_videos, key=lambda v: -(v.get("views") or 0))[:12]:
        retention = video.get("avg_view_percentage")
        lines.append(
            f"- \"{video.get('title', '')}\" — {video.get('views', 0)} view"
            + (f", retensi {retention:.1f}%" if retention is not None else "")
            + f", {video.get('likes') or 0} like"
        )
    return "\n".join(lines)


def recommend(
    episodes: list[dict],
    own_videos: list[dict] | None = None,
    audience: dict | None = None,
    api_key: str | None = None,
) -> str:
    """Minta Gemini memilih episode mana yang layak diklip."""
    if not episodes:
        raise ValueError(
            "Tidak ada episode baru yang memenuhi syarat. Longgarkan batas umur "
            "tayang, atau tambah channel yang dipantau."
        )

    lines = ["=== EPISODE PODCAST TERBARU ==="]
    for episode in episodes[:25]:
        if episode.get("comments_disabled"):
            reaction = "komentar DIMATIKAN (bukan berarti sepi)"
        else:
            comments = episode.get("comments") or 0
            rate = episode.get("comment_rate")
            reaction = f"{comments:,} komentar"
            if rate is not None:
                reaction += f" ({rate * 100:.3f}% dari view)"

        lines.append(
            f"- \"{episode['title']}\" — {episode['channel']}\n"
            f"  {episode['views']:,} view dalam {episode['hours_since']:.0f} jam "
            f"= {episode['velocity']:,.0f} view/jam · "
            f"durasi {episode['duration_seconds'] / 60:.0f} menit\n"
            f"  {episode['likes']:,} like "
            f"({episode.get('like_rate', 0) * 100:.2f}% dari view) · {reaction}"
        )

    lines.append("\n=== PERFORMA CHANNEL CLIPPER (milik user) ===")
    lines.append(_format_own_performance(own_videos or []))

    if audience and audience.get("demographics"):
        top = sorted(
            audience["demographics"], key=lambda r: -r["percentage"]
        )[:3]
        lines.append("\n=== AUDIENS USER ===")
        lines.append(
            ", ".join(f"{r['age']} {r['gender']} {r['percentage']:.0f}%" for r in top)
        )

    lines.append("\nBerikan rekomendasimu sesuai format yang diminta.")

    response, _model = gemini_client.generate(
        "\n".join(lines),
        types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT, max_output_tokens=8192
        ),
        api_key,
    )

    text = (response.text or "").strip()
    if not text:
        raise Exception("Gemini tidak mengembalikan teks. Coba lagi.")
    return text
