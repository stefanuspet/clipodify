"""Signal 2 (bobot 0.25) — energi audio dan BENTUK kurvanya.

Energi rata-rata tidak cukup. Energi yang menanjak lalu memuncak di 80% akhir
berarti ada punchline yang mendarat; energi tinggi rata sepanjang window berarti
orang lagi ngegas terus — sering justru membosankan. Rata-rata tidak bisa
membedakan keduanya, posisi puncak bisa.
"""

import logging
import os
import subprocess
import tempfile

import librosa
import numpy as np

from utils.constants import (
    LAUGHTER_FREQ_LOW,
    LAUGHTER_FREQ_HIGH,
    ENERGY_HOP_SECONDS,
    PAUSE_SILENCE_DB,
    PAUSE_MIN_SECONDS,
)

logger = logging.getLogger(__name__)

NEUTRAL_SCORE = 0.5


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


# Cukup untuk band tawa 1–4 kHz (Nyquist 8 kHz) dan jauh lebih ringan diproses
ANALYSIS_SAMPLE_RATE = 16000


def _load_audio(audio_path: str) -> tuple[np.ndarray, int]:
    """Muat audio lewat ffmpeg → wav sementara.

    yt-dlp menghasilkan .webm/.m4a yang tidak bisa dibaca soundfile, sehingga
    librosa jatuh ke backend audioread — deprecated sejak librosa 0.10 dan akan
    dihapus di 1.0, selain jauh lebih lambat. Melewatkan ffmpeg membuat modul ini
    tidak bergantung pada format sumber.
    """
    tmp_wav = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    tmp_wav.close()

    try:
        subprocess.run(
            [
                "ffmpeg", "-y", "-i", audio_path,
                "-vn", "-ac", "1", "-ar", str(ANALYSIS_SAMPLE_RATE),
                tmp_wav.name,
            ],
            check=True,
            capture_output=True,
        )
        return librosa.load(tmp_wav.name, sr=None, mono=True)
    finally:
        if os.path.exists(tmp_wav.name):
            os.remove(tmp_wav.name)


PAUSE_HOP_SECONDS = 0.02


def detect_pauses(
    audio_path: str,
    silence_db: float = PAUSE_SILENCE_DB,
    min_seconds: float = PAUSE_MIN_SECONDS,
) -> list[float]:
    """Cari titik tengah jeda bicara, dari energi audio. Return daftar detik.

    Ini sumber batas window yang sebenarnya. Transcript tidak bisa dipakai untuk
    itu: faster-whisper menghasilkan segmen berdempet, jadi "jeda antar segmen"
    hampir selalu nol dan `PAUSE_GAP_THRESHOLD` tidak pernah menyala.

    Ambangnya relatif terhadap puncak, bukan dBFS mutlak. Audio podcast punya
    room tone dan kompresi sehingga tidak pernah benar-benar sunyi — pada sampel
    uji, `silencedetect` ffmpeg di -30 dB mutlak menemukan nol jeda, sementara
    ambang relatif yang sama menemukan 11 jeda yang 91%-nya jatuh tepat di batas
    segmen transcript.
    """
    try:
        y, sr = _load_audio(audio_path)
    except Exception as e:
        logger.warning("Deteksi jeda dilewati, audio gagal dimuat: %s", e)
        return []

    if y.size == 0:
        return []

    hop = max(1, int(PAUSE_HOP_SECONDS * sr))
    rms = librosa.feature.rms(y=y, frame_length=hop * 2, hop_length=hop)[0]
    if rms.size == 0 or not np.any(rms > 0):
        return []

    db = librosa.amplitude_to_db(rms, ref=np.max)
    quiet = db < silence_db

    pauses: list[float] = []
    index = 0
    while index < len(quiet):
        if not quiet[index]:
            index += 1
            continue
        end = index
        while end < len(quiet) and quiet[end]:
            end += 1
        if (end - index) * PAUSE_HOP_SECONDS >= min_seconds:
            pauses.append((index + end) / 2 * PAUSE_HOP_SECONDS)
        index = end

    logger.info("Deteksi jeda: %d titik dari %.0f detik audio", len(pauses), len(y) / sr)
    return pauses


def compute_energy_curve(audio_path: str, hop_seconds: float = ENERGY_HOP_SECONDS):
    """Kurva energi RMS per detik, di-z-score terhadap seluruh video.

    Return: (times, z_scores, y, sr)
    Z-score dipakai supaya window bisa dibandingkan antar bagian video, bukan
    terhadap ambang absolut yang bergantung pada mastering tiap video.
    """
    y, sr = _load_audio(audio_path)

    hop_length = max(1, int(sr * hop_seconds))
    rms = librosa.feature.rms(
        y=y, frame_length=hop_length * 2, hop_length=hop_length
    )[0]
    times = librosa.frames_to_time(
        np.arange(len(rms)), sr=sr, hop_length=hop_length
    )

    rms_db = 20 * np.log10(rms + 1e-9)
    z_scores = (rms_db - rms_db.mean()) / (rms_db.std() + 1e-9)

    return times, z_scores, y, sr


def _compute_laughter_score(segment_audio: np.ndarray, sr: int) -> float:
    """Proporsi energi di band 1–4 kHz — rentang tempat tawa menonjol."""
    if segment_audio.size < 2048:
        return 0.0

    stft = np.abs(librosa.stft(segment_audio))
    freqs = librosa.fft_frequencies(sr=sr)

    band = (freqs >= LAUGHTER_FREQ_LOW) & (freqs <= LAUGHTER_FREQ_HIGH)
    if not band.any():
        return 0.0

    band_energy = float(stft[band].sum())
    total_energy = float(stft.sum())

    return _clamp(band_energy / (total_energy + 1e-8))


def _neutral_features() -> dict:
    return {
        "peak_z": 0.0,
        "peak_position": 0.5,
        "shape_score": NEUTRAL_SCORE,
        "laughter_score": 0.0,
    }


def score_windows(audio_path: str, windows: list[dict]) -> list[dict]:
    """Beri skor audio ke tiap window. Audio dimuat sekali untuk semua window."""
    if not windows:
        return windows

    try:
        times, z_scores, y, sr = compute_energy_curve(audio_path)
    except Exception as e:
        logger.warning("Gagal memuat audio (%s), semua score_audio = %.1f",
                       e, NEUTRAL_SCORE)
        for window in windows:
            window["score_audio"] = NEUTRAL_SCORE
            window["audio_features"] = _neutral_features()
        return windows

    for window in windows:
        start = window["start_seconds"]
        end = window["end_seconds"]
        duration = max(end - start, 1e-6)

        try:
            mask = (times >= start) & (times <= end)
            if not mask.any():
                raise ValueError("tidak ada titik kurva di window ini")

            window_times = times[mask]
            window_z = z_scores[mask]

            peak_idx = int(np.argmax(window_z))
            peak_z = float(window_z[peak_idx])
            peak_position = _clamp(
                float(window_times[peak_idx] - start) / duration
            )

            # z=-2 -> 0.0, z=0 -> 0.5, z=+2 -> 1.0
            level_score = _clamp((peak_z + 2) / 4)

            # Puncak di akhir = punchline mendarat. Puncak di awal = energi luruh.
            shape_score = 0.3 + 0.7 * peak_position

            segment_audio = y[int(start * sr):int(end * sr)]
            laughter_score = _compute_laughter_score(segment_audio, sr)

            window["score_audio"] = _clamp(
                level_score * 0.45
                + shape_score * 0.30
                + laughter_score * 0.25
            )
            window["audio_features"] = {
                "peak_z": round(peak_z, 3),
                "peak_position": round(peak_position, 3),
                "shape_score": round(shape_score, 3),
                "laughter_score": round(laughter_score, 3),
            }
        except Exception as e:
            logger.warning("Window %.1f-%.1fs gagal dianalisis (%s), pakai netral",
                           start, end, e)
            window["score_audio"] = NEUTRAL_SCORE
            window["audio_features"] = _neutral_features()

    return windows
