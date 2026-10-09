"""Sumber video dari file lokal, sebagai pasangan `downloader.py` untuk YouTube.

File aslinya tidak pernah disalin ke mana pun. Yang masuk `TMP_DIR` hanya audio
hasil ekstraksi, dan itu dihapus di akhir pipeline — jadi memakai file lokal
tidak menambah apa pun ke storage permanen.
"""

import hashlib
import json
import os
import subprocess

from utils.constants import TMP_DIR
from utils.helpers import ensure_dirs, require_ffmpeg, timestamp_to_seconds


def make_video_id(path: str) -> str:
    """ID sintetis yang stabil untuk satu file.

    Diturunkan dari path absolut, bukan angka acak, supaya file yang sama
    menghasilkan ID yang sama di sesi berikutnya — itulah yang membuat cache
    audio dan hasil analisis sebelumnya bisa dipakai ulang.
    """
    digest = hashlib.sha1(os.path.abspath(path).encode("utf-8")).hexdigest()
    return f"local_{digest[:10]}"


def _run(args: list[str], what: str) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(args, check=True, capture_output=True)
    except subprocess.CalledProcessError as e:
        stderr = (e.stderr or b"").decode("utf-8", "replace")[-400:]
        raise Exception(f"{what} gagal: {stderr}") from e


def probe(path: str) -> dict:
    require_ffmpeg("ffprobe")

    if not os.path.isfile(path):
        raise Exception(f"File tidak ditemukan: {path}")

    # Menggunakan specifier ffprobe yang presisi
    result = _run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration:stream=codec_type,width,height",
            "-of", "json", path,
        ],
        "Membaca info file",
    )

    try:
        info = json.loads(result.stdout.decode("utf-8", "replace"))
    except json.JSONDecodeError as e:
        raise Exception(f"Output ffprobe tidak bisa dibaca untuk {path}") from e

    streams = info.get("streams") or []
    if not any(s.get("codec_type") == "audio" for s in streams):
        raise Exception(
            "File ini tidak punya track audio. Clipodify bekerja dari ucapan, "
            "jadi video tanpa audio tidak bisa dianalisis."
        )

    duration = float((info.get("format") or {}).get("duration") or 0)
    video_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
    resolution = ""
    if video_stream and video_stream.get("width"):
        resolution = f"{video_stream['width']}x{video_stream['height']}"

    return {
        "video_id": make_video_id(path),
        "title": os.path.splitext(os.path.basename(path))[0],
        "duration": int(duration),
        "thumbnail": "",
        "uploader": os.path.dirname(os.path.abspath(path)),
        "upload_date": "",
        "resolution": resolution,
        "comments": [],
        "chapters": [],
    }


def _concat_parts(part_paths: list[str], output_path: str) -> str:
    list_file = output_path + ".concat.txt"
    with open(list_file, "w", encoding="utf-8") as f:
        for path in part_paths:
            # Fixing FFmpeg concat demuxer path escaping rule
            escaped = os.path.abspath(path).replace("\\", "/").replace("'", r"\'")
            f.write(f"file '{escaped}'\n")

    try:
        _run(
            [
                "ffmpeg", "-y", "-f", "concat", "-safe", "0",
                "-i", list_file, "-c", "copy",
                "-movflags", "+faststart", output_path,
            ],
            "Menyambung potongan clip",
        )
    finally:
        if os.path.exists(list_file):
            os.remove(list_file)
        for path in part_paths:
            if os.path.exists(path):
                os.remove(path)

    return output_path


def extract_audio(path: str, video_id: str) -> str:
    """Ekstrak audio ke WAV 16 kHz mono di TMP_DIR. Skip kalau sudah ada.

    16 kHz mono adalah format yang dipakai Whisper maupun analisis energi audio,
    jadi mengekstrak langsung ke sana menghindari satu putaran konversi lagi
    di hilir.
    """
    require_ffmpeg("ffmpeg")
    ensure_dirs()

    output_path = os.path.join(TMP_DIR, f"{video_id}_audio.wav")
    if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
        return output_path

    _run(
        [
            "ffmpeg", "-y", "-i", path,
            "-vn", "-ac", "1", "-ar", "16000",
            "-c:a", "pcm_s16le", output_path,
        ],
        "Ekstraksi audio",
    )

    if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
        raise Exception(f"Ekstraksi audio selesai tapi file kosong: {output_path}")
    return output_path


def _cut_range(
    path: str,
    start: float,
    duration: float,
    output_path: str,
    preview: bool = False,
) -> str:
    """Potong satu rentang dengan re-encode.

    Stream copy tidak dipakai di sini: dengan `-c copy` potongan hanya bisa
    jatuh di keyframe, sehingga awal clip bisa meleset beberapa detik dari hook
    yang dipilih AI. Untuk potongan pendek, re-encode murah dan hasilnya persis.
    `-ss` diletakkan sebelum `-i` supaya seek-nya tetap cepat di file panjang.

    Mode `preview` menurunkan resolusi dan bitrate secara drastis. Itu bukan soal
    estetika: Streamlit memuat seluruh file media ke memori sebelum menyajikannya,
    jadi preview harus kecil supaya tidak menelan RAM sebesar videonya.
    """
    if preview:
        codec_args = [
            "-vf", "scale=-2:360",
            "-c:v", "libx264", "-crf", "30", "-preset", "veryfast",
            "-c:a", "aac", "-b:a", "96k",
        ]
    else:
        codec_args = [
            "-c:v", "libx264", "-crf", "20", "-preset", "veryfast",
            "-c:a", "aac", "-b:a", "192k",
        ]

    _run(
        [
            "ffmpeg", "-y",
            "-ss", f"{start:.3f}", "-i", path, "-t", f"{duration:.3f}",
            *codec_args,
            "-movflags", "+faststart",
            output_path,
        ],
        "Memotong clip",
    )

    if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
        raise Exception(f"Potongan clip kosong: {output_path}")
    return output_path


def _build(path: str, parts: list[dict], output_path: str, preview: bool) -> str:
    require_ffmpeg("ffmpeg")

    if not parts:
        raise ValueError("Daftar bagian clip kosong")
    if not os.path.isfile(path):
        raise Exception(
            f"File sumber sudah tidak ada di {path}. "
            "Clip dipotong langsung dari file aslinya, jadi file itu perlu tetap "
            "di tempatnya selama kamu masih mengunduh hasil."
        )

    ranges = []
    for part in parts:
        start = timestamp_to_seconds(part["start"])
        end = timestamp_to_seconds(part["end"])
        if end <= start:
            raise ValueError(
                f"end ({part['end']}) harus setelah start ({part['start']})"
            )
        ranges.append((start, end - start))

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)

    if len(ranges) == 1:
        start, duration = ranges[0]
        return _cut_range(path, start, duration, output_path, preview=preview)

    stem, ext = os.path.splitext(output_path)
    part_paths = []
    try:
        for index, (start, duration) in enumerate(ranges, 1):
            part_path = f"{stem}.part{index}{ext}"
            part_paths.append(
                _cut_range(path, start, duration, part_path, preview=preview)
            )
        return _concat_parts(part_paths, output_path)
    except Exception:
        for part_path in part_paths:
            if os.path.exists(part_path):
                os.remove(part_path)
        raise


def cut_clip(path: str, parts: list[dict], output_path: str) -> str:
    """Potong clip final dari file lokal. Tanda tangannya sama dengan `download_clip`."""
    return _build(path, parts, output_path, preview=False)


def make_preview(path: str, parts: list[dict], video_id: str, index: int) -> str:
    """Preview kecil di TMP_DIR, dibuat saat diminta lalu dipakai ulang.

    Untuk clip multi-bagian preview ini menampilkan hasil sambungan yang
    sebenarnya — sesuatu yang tidak bisa dilakukan embed YouTube, sehingga di
    sumber lokal kelayakan sambungannya justru bisa dinilai sebelum diunduh.
    """
    ensure_dirs()
    output_path = os.path.join(TMP_DIR, f"{video_id}_preview_{index:02d}.mp4")
    if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
        return output_path
    return _build(path, parts, output_path, preview=True)
