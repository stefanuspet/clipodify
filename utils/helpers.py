import os
import re
import shutil

from utils.constants import OUTPUT_DIR, TMP_DIR

# Karakter yang tidak aman untuk nama file/folder di semua OS (termasuk Windows)
UNSAFE_FILENAME_CHARS = r'[/\\:*?"<>|]'

# Nama terlarang di Windows
WINDOWS_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
    "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
}


def seconds_to_timestamp(seconds: float, include_ms: bool = False) -> str:
    """Mengubah detik (float/int) menjadi format HH:MM:SS atau HH:MM:SS.mmm"""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    
    if include_ms:
        millis = int(round((seconds - int(seconds)) * 1000))
        return f"{hours:02d}:{minutes:02d}:{secs:02d}.{millis:03d}"
    
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def timestamp_to_seconds(timestamp: str) -> float:
    """'00:12:14' -> 734.0. Terima juga format 'MM:SS', 'SS', atau float string '734.5'."""
    if not timestamp or not isinstance(timestamp, str):
        return 0.0

    # Normalisasi koma desimal Indonesia ("14,5" -> "14.5") dan bersihkan spasi
    cleaned = timestamp.strip().replace(",", ".")

    # Kasus jika input berupa string angka murni dalam detik (misal: "734" atau "734.5")
    if ":" not in cleaned:
        try:
            return max(0.0, float(cleaned))
        except ValueError:
            raise ValueError(f"Format timestamp tidak valid: {timestamp!r}")

    parts = [float(p) for p in cleaned.split(":")]
    if len(parts) == 3:
        hours, minutes, secs = parts
    elif len(parts) == 2:
        hours, minutes, secs = 0.0, parts[0], parts[1]
    else:
        raise ValueError(f"Format timestamp tidak valid: {timestamp!r}")

    return max(0.0, hours * 3600 + minutes * 60 + secs)


def extract_video_id(url: str) -> str | None:
    """Ambil video_id dari berbagai bentuk URL YouTube (Desktop, Mobile, Shorts, Embed, Live)."""
    if not url:
        return None

    patterns = [
        r"(?:(?:www\.|m\.)?youtube\.com|(?:www\.)?youtube-nocookie\.com)/watch\?(?:.*&)?v=([A-Za-z0-9_-]{11})",
        r"(?:www\.)?youtu\.be/([A-Za-z0-9_-]{11})",
        r"(?:(?:www\.|m\.)?youtube\.com|(?:www\.)?youtube-nocookie\.com)/(?:embed|shorts|live|v)/([A-Za-z0-9_-]{11})",
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return None


def validate_youtube_url(url: str) -> bool:
    """True jika URL adalah YouTube URL yang video_id-nya bisa diekstrak."""
    return extract_video_id(url) is not None


def youtube_embed_url(
    video_id: str,
    start_seconds: int,
    autoplay: bool = False,
    nonce: int = 0,
) -> str:
    """Embed URL yang tersedia di timestamp tertentu."""
    start = max(0, int(start_seconds))
    url = f"https://www.youtube.com/embed/{video_id}?start={start}&rel=0"
    if autoplay:
        url += "&autoplay=1"
    if nonce:
        url += f"&_r={nonce}"
    return url


def youtube_watch_url(video_id: str, start_seconds: int) -> str:
    """Link ke YouTube biasa di timestamp tertentu, untuk dibuka di tab baru."""
    return f"https://www.youtube.com/watch?v={video_id}&t={max(0, int(start_seconds))}s"


def sanitize_filename(name: str, fallback_name: str = "unnamed_file") -> str:
    """Judul video -> string aman untuk nama file/folder, max 100 karakter.

    Menangani nama file khusus OS Windows dan mengembalikan fallback jika hasil sanitasi kosong.
    """
    if not name:
        return fallback_name

    # Ganti karakter tidak aman dengan underscore
    safe = re.sub(UNSAFE_FILENAME_CHARS, "_", name)
    safe = safe.replace(" ", "_")
    
    # Hapus titik dan underscore di awal/akhir
    safe = safe.strip("._")

    # Batasi panjang maksimal 100 karakter
    safe = safe[:100].strip("._")

    # Cegah hasil kosong atau nama terlarang Windows
    if not safe or safe.upper() in WINDOWS_RESERVED_NAMES:
        return fallback_name

    return safe


def require_ffmpeg(*binaries: str) -> None:
    """Pastikan tool ffmpeg ada di PATH sebelum dipakai."""
    missing = [name for name in (binaries or ("ffmpeg",)) if not shutil.which(name)]
    if missing:
        raise RuntimeError(
            f"{', '.join(missing)} tidak ditemukan di PATH. "
            "Pasang ffmpeg lebih dulu — Linux: `sudo apt install ffmpeg`, "
            "Windows: `winget install Gyan.FFmpeg` lalu buka ulang terminal."
        )


def ensure_dirs() -> None:
    """Pastikan OUTPUT_DIR dan TMP_DIR ada."""
    for directory in (OUTPUT_DIR, TMP_DIR):
        os.makedirs(directory, exist_ok=True)


if __name__ == "__main__":
    print("seconds_to_timestamp(734.5):", seconds_to_timestamp(734.5))
    print("timestamp_to_seconds('00:12:14'):", timestamp_to_seconds("00:12:14"))
    print("timestamp_to_seconds('12:14'):", timestamp_to_seconds("12:14"))
    print("timestamp_to_seconds('734.5'):", timestamp_to_seconds("734.5"))

    urls = [
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://m.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://youtu.be/dQw4w9WgXcQ",
        "https://www.youtube.com/watch?feature=shared&v=dQw4w9WgXcQ",
        "https://www.youtube.com/shorts/dQw4w9WgXcQ",
        "https://vimeo.com/12345",
        "bukan url sama sekali",
    ]
    for u in urls:
        print(f"  {u!r:60} -> id={extract_video_id(u)} valid={validate_youtube_url(u)}")

    print("youtube_embed_url:", youtube_embed_url("dQw4w9WgXcQ", 734))
    print("sanitize_filename (normal):", sanitize_filename("My Video: Part 1/2"))
    print("sanitize_filename (unsafe):", sanitize_filename("???///"))

    ensure_dirs()
    print("ensure_dirs OK:", os.path.isdir(OUTPUT_DIR), os.path.isdir(TMP_DIR))