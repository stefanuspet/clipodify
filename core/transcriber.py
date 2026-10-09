"""faster-whisper wrapper. Satu-satunya komponen yang menyentuh GPU."""

import gc
import logging
import os

import torch
from faster_whisper import WhisperModel

from utils.constants import (
    WHISPER_COMPUTE_TYPE,
    WHISPER_DEVICE,
    WHISPER_LANGUAGE,
    WHISPER_MODEL,
)

logger = logging.getLogger(__name__)


def _resolve_device() -> tuple[str, str]:
    """Turun ke CPU kalau CUDA diminta tapi tidak tersedia."""
    device = WHISPER_DEVICE
    compute_type = WHISPER_COMPUTE_TYPE

    if device == "cuda" and not torch.cuda.is_available():
        logger.warning("CUDA tidak tersedia, fallback ke CPU (transcribe akan lambat)")
        device = "cpu"
        # float16 tidak didukung di CPU
        compute_type = "int8"

    return device, compute_type


def transcribe(audio_path: str, language: str | None = WHISPER_LANGUAGE) -> dict:
    """Transcribe audio file. Model di-unload dari VRAM setelah selesai.

    `language` mengunci bahasanya. Isi None untuk deteksi otomatis — tapi sadari
    bahwa deteksi Whisper cuma membaca 30 detik pertama, dan kalau tebakannya
    meleset hasilnya BUKAN transcript yang salah eja, melainkan terjemahan utuh
    ke bahasa yang salah. Kegagalan itu tidak melempar error dan tidak terlihat
    sampai title/description terlanjur ditulis dalam bahasa asing.

    Return:
        {"text": str, "language": str, "language_probability": float,
         "language_forced": bool,
         "segments": [{"start": float, "end": float, "text": str, "words": list}],
         "words": [{"start": float, "end": float, "word": str, "probability": float}]}
    """
    if not os.path.exists(audio_path):
        raise FileNotFoundError(f"Audio file tidak ditemukan: {audio_path}")

    device, compute_type = _resolve_device()

    model = WhisperModel(
        WHISPER_MODEL,
        device=device,
        compute_type=compute_type,
    )

    try:
        segments_gen, info = model.transcribe(
            audio_path,
            language=language or None,
            beam_size=5,
            # Diaktifkan agar validator dapat melakukan word-level snapping presisi
            word_timestamps=True,
            # vad_filter yang menghasilkan jeda hening — inilah yang dipakai
            # core/windowing.py untuk menentukan batas window natural.
            vad_filter=True,
            vad_parameters=dict(min_silence_duration_ms=500),
        )

        segments = []
        all_words = []
        parts = []

        for seg in segments_gen:
            text = seg.text.strip()

            # Ekstraksi word timestamps per segmen
            seg_words = []
            if getattr(seg, "words", None):
                for w in seg.words:
                    w_dict = {
                        "start": w.start,
                        "end": w.end,
                        # Jangan hapus spasi bawaan jika digunakan untuk penggabungan kata/subtitle,
                        # tetapi jika hanya ingin string bersih untuk kata tersebut:
                        "word": w.word.strip(),
                        "probability": w.probability,
                    }
                    seg_words.append(w_dict)
                    all_words.append(w_dict)

            segments.append({
                "start": seg.start,
                "end": seg.end,
                "text": text,
                "words": seg_words,
            })
            parts.append(text)

        return {
            "text": " ".join(parts),
            "language": info.language,
            "language_probability": getattr(info, "language_probability", 1.0),
            "language_forced": bool(language),
            "segments": segments,
            "words": all_words,
        }
    finally:
        # Unload MANDATORY & Paksa Garbage Collection untuk membebaskan C++/CTranslate2 memory
        del model
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()


def get_whisper_model_info() -> dict:
    """Info model untuk ditampilkan di UI."""
    device, compute_type = _resolve_device()
    return {
        "model": WHISPER_MODEL,
        "device": device,
        "compute_type": compute_type,
    }