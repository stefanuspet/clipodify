"""Satu pintu untuk dua jenis sumber video: URL YouTube dan file lokal.

Pipeline hanya menyentuh sumbernya di tiga titik — ambil metadata, siapkan
audio, potong clip. Ketiganya dikumpulkan di sini supaya `app.py` tidak perlu
bercabang di mana-mana, dan supaya menambah jenis sumber baru nanti cukup
menyentuh satu file.

Nilai `source` yang beredar di seluruh aplikasi berupa string: URL YouTube apa
adanya, atau path absolut file lokal. Itu juga yang disimpan di kolom
`jobs.url`, sehingga cache dan History bekerja sama persis untuk keduanya.
"""

import os

from core import downloader, local_media
from utils.constants import MAX_COMMENTS
from utils.helpers import validate_youtube_url

YOUTUBE = "youtube"
LOCAL = "local"


def _clean_input(source: str) -> str:
    """Bersihkan spasi dan tanda kutip dari input mentah."""
    return (source or "").strip().strip("'\"")


def detect_type(source: str) -> str:
    """Tentukan jenis sumber dari nilainya sendiri."""
    cleaned = _clean_input(source)
    return YOUTUBE if validate_youtube_url(cleaned) else LOCAL


def normalize(source: str) -> str:
    """Rapikan input user jadi bentuk kanonik yang layak disimpan.

    Path lokal diubah jadi absolut supaya satu file selalu menghasilkan job yang
    sama, tidak peduli dari direktori mana Streamlit dijalankan. Tanda kutip ikut
    dibuang di awal agar URL terbungkus kutip tidak salah terdeteksi.
    """
    cleaned = _clean_input(source)
    if not cleaned:
        return ""

    if detect_type(cleaned) == YOUTUBE:
        return cleaned

    return os.path.abspath(os.path.expanduser(cleaned))


def describe(source: str) -> str:
    """Label pendek jenis sumber, untuk ditampilkan di UI."""
    return "YouTube" if detect_type(source) == YOUTUBE else "File lokal"


def validate(source: str) -> str | None:
    """Return pesan error kalau sumber tidak bisa dipakai, atau None kalau aman."""
    cleaned = _clean_input(source)
    if not cleaned:
        return "Sumber video belum diisi."

    if detect_type(cleaned) == YOUTUBE:
        return None

    # Berikan info lebih ramah jika user memasukkan URL non-YouTube
    if cleaned.startswith(("http://", "https://")):
        return "URL web yang didukung saat ini hanya YouTube."

    if not os.path.exists(cleaned):
        return f"File tidak ditemukan: {cleaned}"
    if not os.path.isfile(cleaned):
        return f"Ini folder, bukan file video: {cleaned}"
    if os.path.getsize(cleaned) == 0:
        return f"File kosong: {cleaned}"
    return None


def get_metadata(source: str, with_comments: bool = False) -> dict:
    """Metadata sumber dalam bentuk seragam.

    `with_comments` hanya berpengaruh untuk YouTube. File lokal selalu
    mengembalikan comments dan chapters kosong — dan itu bukan kasus khusus,
    karena `fusion.compute_weights()` memang sudah menormalisasi ulang bobot
    saat sinyal komentar tidak tersedia.
    """
    if detect_type(source) == LOCAL:
        return local_media.probe(source)

    if with_comments:
        return downloader.fetch_metadata_with_comments(source, max_comments=MAX_COMMENTS)

    meta = downloader.get_video_metadata(source)
    meta["comments"] = []
    meta["chapters"] = []
    return meta


def prepare_audio(source: str, video_id: str) -> str:
    """Siapkan audio untuk transcribe, dan kembalikan path-nya di TMP_DIR."""
    if detect_type(source) == LOCAL:
        return local_media.extract_audio(source, video_id)
    return downloader.download_audio(source, video_id)


def extract_clip(source: str, parts: list[dict], output_path: str) -> str:
    """Hasilkan file clip final dari daftar bagian."""
    if detect_type(source) == LOCAL:
        return local_media.cut_clip(source, parts, output_path)
    return downloader.download_clip(source, parts, output_path)


def needs_full_download(source: str) -> bool:
    """Apakah video utuh masih perlu diunduh untuk sumber ini.

    File lokal sudah utuh di disk, jadi jawabannya False — menyalinnya ke folder
    output cuma menggandakan file besar tanpa memberi apa pun yang belum ada.
    """
    return detect_type(source) == YOUTUBE


def download_full(source: str, output_path: str, progress_hook=None) -> str:
    """Sediakan video utuh dan kembalikan path-nya.

    Untuk file lokal ini TIDAK menyalin apa pun — path sumbernya dikembalikan apa
    adanya, konsisten dengan janji bahwa file lokal hanya dibaca di tempat.
    """
    if detect_type(source) == LOCAL:
        return source
    return downloader.download_full(source, output_path, progress_hook)


def make_preview(source: str, parts: list[dict], video_id: str, index: int) -> str:
    """Preview ringan sebuah clip untuk sumber lokal.

    Sumber YouTube tidak memakai jalur ini — di sana preview cukup memakai
    pemutar embed, yang gratis dan tidak perlu memproses apa pun.
    """
    if detect_type(source) != LOCAL:
        raise ValueError("Preview hanya untuk sumber lokal")
    return local_media.make_preview(source, parts, video_id, index)