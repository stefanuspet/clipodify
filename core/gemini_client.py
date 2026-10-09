"""Satu pintu ke Gemini, dengan pindah model otomatis saat kuota habis.

Free tier memberi jatah HARIAN yang kecil dan terpisah per model — 20 request
per hari untuk gemini-3.6-flash, tapi 500 untuk model Lite. Menunggu lalu
mencoba lagi model yang sama tidak menolong sama sekali kalau yang habis adalah
jatah harian: besok baru pulih.

Karena itu 429 diperlakukan berbeda dari error lain. Error jaringan dan server
sibuk DIULANG di model yang sama; kuota habis langsung PINDAH ke model
berikutnya. Membedakan keduanya adalah inti modul ini.
"""

import logging
import time

from google import genai
from google.genai import types

from utils.constants import GEMINI_API_KEY, GEMINI_FALLBACK_MODELS, GEMINI_MODEL

logger = logging.getLogger(__name__)

MAX_RETRIES_PER_MODEL = 2
RETRY_BACKOFF_SECONDS = 2

# Model yang benar-benar mengerjakan panggilan terakhir. Dipakai UI untuk memberi
# tahu user saat hasil dikerjakan model cadangan — perubahan kualitas tanpa
# penjelasan jauh lebih membingungkan daripada peringatan yang jujur.
# Variabel modul aman di sini karena tool ini lokal dan satu pemakai.
LAST_MODEL: str | None = None


class AllModelsExhaustedError(Exception):
    """Semua model di rantai menolak. Biasanya berarti kuota harian habis."""


def model_chain() -> list[str]:
    """Urutan model yang dicoba: kualitas menurun, jatah harian menaik."""
    chain = [GEMINI_MODEL] + [
        m for m in GEMINI_FALLBACK_MODELS if m and m != GEMINI_MODEL
    ]
    seen, ordered = set(), []
    for model in chain:
        if model not in seen:
            seen.add(model)
            ordered.append(model)
    return ordered


def resolve_api_key(explicit: str | None = None) -> str:
    """Key yang dipakai untuk pemanggilan berikutnya.

    Urutannya: key yang dioper langsung, lalu key aktif yang dikelola dari
    halaman Settings, terakhir `.env`. Dibaca tiap panggilan, bukan sekali saat
    import — itulah yang membuat pergantian key di UI langsung berlaku tanpa
    perlu me-restart Streamlit.
    """
    if explicit and explicit.strip():
        return explicit.strip()

    try:
        from db.database import get_active_api_key
        stored = get_active_api_key()
    except Exception:
        stored = None

    if stored:
        return stored

    if GEMINI_API_KEY:
        return GEMINI_API_KEY

    raise ValueError(
        "Belum ada Gemini API key. Tambahkan di halaman Settings, atau isi "
        "GEMINI_API_KEY di .env."
    )


def _is_quota_error(message: str) -> bool:
    return "RESOURCE_EXHAUSTED" in message or "429" in message


def _is_key_error(message: str) -> bool:
    return "API_KEY_INVALID" in message or "API key not valid" in message


def _is_missing_model(message: str) -> bool:
    return "NOT_FOUND" in message or "404" in message


def generate(
    contents,
    config: types.GenerateContentConfig,
    api_key: str | None = None,
    models: list[str] | None = None,
) -> tuple[object, str]:
    """Panggil Gemini, pindah model otomatis kalau perlu.

    Return (response, nama_model_yang_berhasil) supaya pemanggil bisa memberi
    tahu user model mana yang sebenarnya dipakai — kalau hasilnya turun kualitas,
    user berhak tahu penyebabnya tanpa harus menebak.
    """
    global LAST_MODEL
    client = genai.Client(api_key=resolve_api_key(api_key))
    chain = models or model_chain()
    problems: list[str] = []

    for model in chain:
        for attempt in range(MAX_RETRIES_PER_MODEL):
            try:
                response = client.models.generate_content(
                    model=model, contents=contents, config=config
                )
                if model != chain[0]:
                    logger.warning(
                        "Model utama %s tidak terpakai, jatuh ke %s", chain[0], model
                    )
                LAST_MODEL = model
                return response, model

            except Exception as e:
                message = str(e)

                # Key salah tidak akan membaik di model mana pun. Berhenti
                # sekarang, jangan bakar seluruh rantai untuk error yang sama.
                if _is_key_error(message):
                    raise ValueError(
                        "Gemini API key ditolak. Cek key aktif di halaman Settings."
                    ) from e

                if _is_quota_error(message) or _is_missing_model(message):
                    reason = "kuota habis" if _is_quota_error(message) else "tidak tersedia"
                    logger.info("Model %s %s, pindah ke berikutnya", model, reason)
                    problems.append(f"{model}: {reason}")
                    break  # langsung model berikutnya, retry tidak menolong

                if attempt < MAX_RETRIES_PER_MODEL - 1:
                    time.sleep(RETRY_BACKOFF_SECONDS * (attempt + 1))
                    continue

                logger.warning("Model %s gagal: %s", model, message[:200])
                problems.append(f"{model}: {message[:80]}")

    raise AllModelsExhaustedError(
        "Semua model Gemini menolak permintaan ini.\n"
        + "\n".join(f"- {p}" for p in problems)
        + "\n\nKalau semuanya menyebut kuota, jatah harian free tier sudah habis "
        "— pulih otomatis besok, atau pakai API key lain di halaman Settings."
    )


def test_api_key(api_key: str) -> tuple[bool, str]:
    """Uji satu key tanpa memakai jatah generate. Return (berhasil, pesan)."""
    try:
        client = genai.Client(api_key=api_key)
        client.models.get(model=GEMINI_MODEL)
        return True, f"Key valid, model '{GEMINI_MODEL}' bisa diakses."
    except Exception as e:
        message = str(e)
        if _is_key_error(message):
            return False, "Key ditolak Gemini — cek lagi salinannya."
        if _is_missing_model(message):
            return False, (
                f"Key valid, tapi model '{GEMINI_MODEL}' tidak tersedia untuk key "
                f"ini. Ganti GEMINI_MODEL di .env."
            )
        return False, f"Gagal menguji key: {message[:200]}"
