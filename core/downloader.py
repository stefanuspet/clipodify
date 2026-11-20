"""Wrapper yt-dlp: metadata, komentar, audio, dan potongan clip.

Pipeline-nya sendiri tidak pernah mengunduh video penuh — hanya audio untuk
transcribe, dan potongan pendek saat user klik Download. `download_full()` ada
di luar jalur itu: dipanggil hanya kalau user memintanya sendiri dari UI.
"""

import glob
import os
import subprocess

import yt_dlp

from utils.constants import (
    MAX_COMMENTS,
    TMP_DIR,
    YTDLP_COOKIES_FROM_BROWSER,
    YTDLP_COOKIE_FILE,
)
from utils.helpers import timestamp_to_seconds, ensure_dirs


def cookie_opts() -> dict:
    """Opsi cookie untuk yt-dlp, kosong kalau tidak dikonfigurasi.

    Tanpa cookie, permintaan ke YouTube bersifat anonim — dan setelah beberapa
    kali unduh beruntun dari satu IP, YouTube menjawab semuanya dengan "Sign in
    to confirm you're not a bot". Blokirnya berlaku untuk SEMUA video, jadi
    gejalanya terlihat seperti aplikasinya yang rusak, padahal bukan.
    """
    if YTDLP_COOKIES_FROM_BROWSER:
        return {"cookiesfrombrowser": (YTDLP_COOKIES_FROM_BROWSER,)}
    if YTDLP_COOKIE_FILE and os.path.exists(YTDLP_COOKIE_FILE):
        return {"cookiefile": YTDLP_COOKIE_FILE}
    return {}


BASE_OPTS = {
    "quiet": True,
    "no_warnings": True,
    "nocheckcertificate": True,
    **cookie_opts(),
}


def _to_metadata(info: dict) -> dict:
    return {
        "video_id": info.get("id"),
        "title": info.get("title") or "",
        "duration": int(info.get("duration") or 0),
        "thumbnail": info.get("thumbnail") or "",
        "uploader": info.get("uploader") or "",
        "upload_date": info.get("upload_date") or "",
    }


def get_video_metadata(url: str) -> dict:
    """Ambil metadata video tanpa mengunduh apa pun."""
    try:
        with yt_dlp.YoutubeDL(BASE_OPTS) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as e:
        raise Exception(f"Gagal mengambil metadata video: {e}") from e

    return _to_metadata(info)


def fetch_metadata_with_comments(url: str, max_comments: int = MAX_COMMENTS) -> dict:
    """Metadata + komentar + chapters dalam satu call. Sumber Signal 1.

    Fetch komentar bisa lambat (10–40 detik) — itu normal, bukan hang.
    Kalau gagal (komentar dimatikan, rate limit, timeout), fungsi ini TIDAK
    raise: dia jatuh balik ke metadata biasa dengan comments/chapters kosong.
    Video tanpa komentar tetap harus bisa diproses.
    """
    opts = {
        **BASE_OPTS,
        "getcomments": True,
        "extractor_args": {
            "youtube": {
                "comment_sort": ["top"],
                "max_comments": [str(max_comments)],
                "player_client": ["android", "web"],
            }
        },
    }

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception:
        info = None

    if info is None:
        meta = get_video_metadata(url)
        meta["comments"] = []
        meta["chapters"] = []
        return meta

    meta = _to_metadata(info)

    comments = []
    for c in info.get("comments") or []:
        comments.append({
            "text": c.get("text") or "",
            "like_count": c.get("like_count") or 0,
        })

    chapters = []
    for ch in info.get("chapters") or []:
        chapters.append({
            "start_time": ch.get("start_time") or 0,
            "end_time": ch.get("end_time") or 0,
            "title": ch.get("title") or "",
        })

    meta["comments"] = comments
    meta["chapters"] = chapters
    return meta


def _find_existing_audio(video_id: str) -> str | None:
    matches = glob.glob(os.path.join(TMP_DIR, f"{video_id}_audio.*"))
    for path in matches:
        if os.path.getsize(path) > 0:
            return path
    return None


def download_audio(url: str, video_id: str) -> str:
    """Download audio only ke TMP_DIR. Skip kalau file sudah ada."""
    ensure_dirs()

    existing = _find_existing_audio(video_id)
    if existing:
        return existing

    opts = {
        **BASE_OPTS,
        "format": "bestaudio/best",
        "outtmpl": os.path.join(TMP_DIR, f"{video_id}_audio.%(ext)s"),
    }

    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])

    path = _find_existing_audio(video_id)
    if not path:
        raise Exception(f"Download audio selesai tapi file tidak ditemukan: {video_id}")
    return path


def download_full(url: str, output_path: str, progress_hook=None) -> str:
    """Unduh video utuh, kualitas terbaik. Return path file yang benar-benar jadi.

    Berbeda dari `download_clip()`, ini tidak dipakai pipeline. Ukurannya bisa
    ratusan MB sampai beberapa GB, jadi pemanggilnya WAJIB memberi tahu user apa
    yang akan terjadi sebelum memanggil.
    """
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)

    opts = {
        **BASE_OPTS,
        "format": "bestvideo+bestaudio/best",
        "merge_output_format": "mp4",
        "outtmpl": output_path,
    }
    if progress_hook:
        opts["progress_hooks"] = [progress_hook]

    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])

    return _resolve_written_file(output_path)


def _download_range(url: str, start: float, end: float, output_path: str) -> str:
    # Prioritas H.264 (avc1) karena support stream copy + keyframe cut
    # VP9/AV1 video-only stream gagal saat range download dengan force_keyframes
    VIDEO_FORMAT = (
        "bestvideo[height<=1080][vcodec^=avc1][ext=mp4]+bestaudio[ext=m4a]"
        "/bestvideo[height<=720][vcodec^=avc1][ext=mp4]+bestaudio[ext=m4a]"
        "/bestvideo[height<=480][vcodec^=avc1][ext=mp4]+bestaudio[ext=m4a]"
        "/bestvideo[vcodec^=avc1]+bestaudio"
        "/bestvideo+bestaudio/best"
    )
    opts = {
        **BASE_OPTS,
        "format": VIDEO_FORMAT,
        "merge_output_format": "mp4",
        "download_ranges": yt_dlp.utils.download_range_func(None, [(start, end)]),
        "force_keyframes_at_cuts": True,
        "outtmpl": output_path,
        "quiet": False,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])
    return _resolve_written_file(output_path)


def _resolve_written_file(output_path: str) -> str:
    """Path yang benar-benar ditulis yt-dlp.

    Ekstensi bisa berubah saat merge, jadi path yang kita minta belum tentu path
    yang jadi. Tanpa pemeriksaan ini, UI menampilkan "berhasil tersimpan" untuk
    file yang tidak pernah ada.
    """
    if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
        return output_path

    stem = os.path.splitext(output_path)[0]
    for candidate in glob.glob(f"{stem}.*"):
        if os.path.getsize(candidate) > 0:
            return candidate

    raise Exception(
        f"yt-dlp selesai tapi file tidak ditemukan di {output_path}. "
        f"Cek apakah ffmpeg terpasang dan folder tujuan bisa ditulis."
    )


def _concat_parts(part_paths: list[str], output_path: str) -> str:
    """Sambung beberapa potongan jadi satu file, tanpa re-encode.

    Semua potongan berasal dari sumber yang sama sehingga parameter codec-nya
    identik — concat demuxer dengan stream copy aman dan menjaga kualitas asli.
    """
    list_file = output_path + ".concat.txt"
    with open(list_file, "w", encoding="utf-8") as f:
        for path in part_paths:
            escaped = os.path.abspath(path).replace("'", r"'\''")
            f.write(f"file '{escaped}'\n")

    try:
        subprocess.run(
            [
                "ffmpeg", "-y", "-f", "concat", "-safe", "0",
                "-i", list_file, "-c", "copy", output_path,
            ],
            check=True,
            capture_output=True,
        )
    except subprocess.CalledProcessError as e:
        stderr = (e.stderr or b"").decode("utf-8", "replace")[-400:]
        raise Exception(f"Gagal menyambung potongan clip: {stderr}") from e
    finally:
        os.remove(list_file)
        for path in part_paths:
            if os.path.exists(path):
                os.remove(path)

    return output_path


def download_clip(url: str, parts: list[dict], output_path: str) -> str:
    """Download clip sesuai daftar bagian, kualitas original, no re-encode.

    `parts` berisi dict dengan `start`/`end` bertipe "HH:MM:SS". Kalau lebih dari
    satu, tiap bagian diunduh terpisah lalu disambung — inilah yang membuat clip
    bisa membuang bagian melantur di tengah.
    """
    if not parts:
        raise ValueError("Daftar bagian clip kosong")

    ranges = []
    for part in parts:
        start_seconds = timestamp_to_seconds(part["start"])
        end_seconds = timestamp_to_seconds(part["end"])
        if end_seconds <= start_seconds:
            raise ValueError(
                f"end ({part['end']}) harus setelah start ({part['start']})"
            )
        ranges.append((start_seconds, end_seconds))

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)

    if len(ranges) == 1:
        start_seconds, end_seconds = ranges[0]
        return _download_range(url, start_seconds, end_seconds, output_path)

    stem, ext = os.path.splitext(output_path)
    part_paths = []
    try:
        for index, (start_seconds, end_seconds) in enumerate(ranges, 1):
            part_path = f"{stem}.part{index}{ext}"
            part_paths.append(_download_range(url, start_seconds, end_seconds, part_path))
        return _concat_parts(part_paths, output_path)
    except Exception:
        for path in part_paths:
            if os.path.exists(path):
                os.remove(path)
        raise


def cleanup_tmp(video_id: str) -> None:
    """Hapus semua file tmp milik video_id ini."""
    for path in glob.glob(os.path.join(TMP_DIR, f"{video_id}_*")):
        try:
            os.remove(path)
        except OSError:
            pass