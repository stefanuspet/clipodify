"""Audio emotion analyzer — deteksi energi, pitch, dan emosi dari audio track.

Fitur akustik yang diekstrak per window:
  rms_energy    → seberapa keras/keras suara (0-1)
  pitch_hz      → pitch dominan dalam Hz
  pitch_std     → variasi pitch (tinggi = ekspresif/lucu)
  spectral_cent → "brightness" suara (tinggi = tajam/marah)
  zcr           → zero crossing rate (tinggi = suara keras/noise)
  speech_ratio  → proporsi frame yang aktif (berbicara vs diam)

Rule-based emotion mapping:
  angry/intense → energy tinggi + pitch tinggi + spectral tinggi
  sad/calm      → energy rendah + pitch rendah + zcr rendah
  excited/funny → energy tinggi + pitch_std tinggi (variasi banyak)
  laughing      → pola burst ritmis (energy naik-turun cepat ~3-5Hz)
  neutral       → semua nilai medium
"""

from __future__ import annotations

import logging
import os
import subprocess
import tempfile
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

_SR          = 16000   # sample rate untuk analisis (downsampled dari original)
_WINDOW_S    = 0.5     # ukuran window analisis (detik)
_HOP_S       = 0.25    # step antar window (50% overlap)
_MIN_SEG_DUR = 1.0     # minimum durasi segmen sebelum di-merge


def analyze_audio(
    video_path: str,
    window_s: float = _WINDOW_S,
    hop_s: float = _HOP_S,
) -> dict[str, Any]:
    """Analisis audio dari video dan kembalikan timeline emosi.

    Returns:
        {
          "duration_s": float,
          "windows": [ {start, end, energy, pitch_hz, pitch_std,
                        speech_ratio, emotion, intensity} ],
          "segments": [ {start, end, emotion, avg_energy, avg_pitch,
                         intensity, description} ],
          "summary": {
            "dominant_emotion": str,
            "avg_energy": float,
            "peak_moments": [ {time, emotion, intensity} ],
            "emotion_counts": {emotion: count},
          }
        }
    """
    try:
        import librosa
    except ImportError as e:
        raise RuntimeError(f"librosa tidak tersedia: {e}") from e

    # Ekstrak audio WAV dari video via FFmpeg
    wav_path = _extract_audio_wav(video_path)
    try:
        y, sr = librosa.load(wav_path, sr=_SR, mono=True)
    finally:
        try:
            os.unlink(wav_path)
        except Exception:
            pass

    duration_s = len(y) / sr
    win_samples = int(window_s * sr)
    hop_samples = int(hop_s * sr)

    windows = _analyze_windows(y, sr, win_samples, hop_samples, librosa)
    segments = _merge_segments(windows, _MIN_SEG_DUR)
    summary  = _build_summary(windows, segments)

    return {
        "duration_s": round(duration_s, 2),
        "windows":    windows,
        "segments":   segments,
        "summary":    summary,
    }


# ---------------------------------------------------------------- window analysis

def _analyze_windows(
    y: np.ndarray, sr: int,
    win_samples: int, hop_samples: int,
    librosa,
) -> list[dict]:
    """Analisis tiap window dan ekstrak fitur akustik."""
    # Pre-compute spectral features sekaligus (lebih efisien)
    rms_frames  = librosa.feature.rms(y=y, frame_length=win_samples, hop_length=hop_samples)[0]
    zcr_frames  = librosa.feature.zero_crossing_rate(y, frame_length=win_samples, hop_length=hop_samples)[0]
    cent_frames = librosa.feature.spectral_centroid(y=y, sr=sr, n_fft=win_samples, hop_length=hop_samples)[0]

    # Pitch extraction (pyin lebih akurat untuk suara manusia)
    try:
        f0, voiced_flag, _ = librosa.pyin(
            y, fmin=librosa.note_to_hz("C2"),
            fmax=librosa.note_to_hz("C7"),
            frame_length=win_samples, hop_length=hop_samples,
            sr=sr,
        )
    except Exception:
        f0          = np.zeros(len(rms_frames))
        voiced_flag = np.zeros(len(rms_frames), dtype=bool)

    n_frames = len(rms_frames)
    # Normalize RMS ke 0-1 relatif terhadap max dalam video
    rms_max = rms_frames.max() if rms_frames.max() > 0 else 1.0

    windows: list[dict] = []
    for i in range(n_frames):
        t_start = round(i * hop_samples / sr, 3)
        t_end   = round(t_start + win_samples / sr, 3)

        energy  = float(rms_frames[i] / rms_max)
        zcr     = float(zcr_frames[i]) if i < len(zcr_frames) else 0.0
        centroid = float(cent_frames[i]) if i < len(cent_frames) else 0.0

        # Pitch: ambil F0 dalam window ini
        f0_window = f0[i:i+1] if i < len(f0) else np.array([0.0])
        voiced    = voiced_flag[i] if i < len(voiced_flag) else False
        pitch_hz  = float(f0_window[0]) if voiced and not np.isnan(f0_window[0]) else 0.0

        # Pitch std: ambil beberapa frame sekitar untuk hitung variasi
        f0_slice  = f0[max(0, i-3):i+4]
        valid_f0  = f0_slice[~np.isnan(f0_slice) & (f0_slice > 0)]
        pitch_std = float(np.std(valid_f0)) if len(valid_f0) > 1 else 0.0

        # Speech ratio: proporsi frame voiced di sekitar window
        voiced_slice  = voiced_flag[max(0, i-2):i+3]
        speech_ratio  = float(voiced_slice.mean()) if len(voiced_slice) > 0 else 0.0

        emotion, intensity = _classify_emotion(
            energy, pitch_hz, pitch_std, centroid, zcr, speech_ratio
        )

        windows.append({
            "start":        t_start,
            "end":          t_end,
            "energy":       round(energy, 3),
            "pitch_hz":     round(pitch_hz, 1),
            "pitch_std":    round(pitch_std, 1),
            "spectral_cent": round(centroid, 0),
            "speech_ratio": round(speech_ratio, 2),
            "emotion":      emotion,
            "intensity":    round(intensity, 3),
        })

    return windows


# ---------------------------------------------------------------- emotion classifier

def _classify_emotion(
    energy: float,
    pitch_hz: float,
    pitch_std: float,
    centroid: float,
    zcr: float,
    speech_ratio: float,
) -> tuple[str, float]:
    """Rule-based mapping fitur akustik → (emotion, intensity 0-1)."""

    # Normalisasi centroid ke 0-1 (range tipikal 500-4000 Hz)
    cent_norm = min(1.0, max(0.0, (centroid - 500) / 3500))
    # Normalisasi pitch (range tipikal 80-400 Hz untuk suara manusia)
    pitch_norm = min(1.0, max(0.0, (pitch_hz - 80) / 320)) if pitch_hz > 0 else 0.0
    # Normalisasi pitch_std (variance tinggi = ekspresif)
    pitch_var  = min(1.0, pitch_std / 80.0)

    # Silence
    if energy < 0.08:
        return "silence", 0.0

    # Tidak ada speech terdeteksi (musik/ambient/efek suara) → classify dari energy
    if speech_ratio < 0.1:
        if energy > 0.75:
            return "excited", round(energy * 0.75, 3)
        if energy > 0.4:
            return "neutral", round(energy * 0.5, 3)
        return "neutral", round(energy * 0.3, 3)

    # Laughter detection: energy burst dengan variasi pitch tinggi + zcr tinggi
    laugh_score = energy * 0.4 + pitch_var * 0.4 + zcr * 20 * 0.2
    if laugh_score > 0.65 and pitch_var > 0.3 and energy > 0.5:
        return "laughing", round(min(1.0, laugh_score), 3)

    # Angry / intense: energy tinggi + centroid tinggi + pitch tinggi
    angry_score = energy * 0.4 + cent_norm * 0.3 + pitch_norm * 0.3
    if angry_score > 0.65 and energy > 0.7:
        return "angry", round(min(1.0, angry_score), 3)

    # Excited / energetic: energy tinggi + pitch variasi sedang
    excited_score = energy * 0.5 + pitch_var * 0.3 + pitch_norm * 0.2
    if excited_score > 0.55 and energy > 0.55:
        return "excited", round(min(1.0, excited_score), 3)

    # Sad / melancholic: energy rendah + pitch rendah + speech lambat
    sad_score = (1 - energy) * 0.5 + (1 - pitch_norm) * 0.3 + (1 - speech_ratio) * 0.2
    if sad_score > 0.60 and energy < 0.35:
        return "sad", round(min(1.0, sad_score * 0.7), 3)  # scale down biar tidak overdramatic

    # Calm / neutral speaking
    return "neutral", round(energy * 0.5 + speech_ratio * 0.3, 3)


# ---------------------------------------------------------------- segment merger

def _merge_segments(windows: list[dict], min_dur: float) -> list[dict]:
    """Gabungkan window berurutan dengan emosi sama → segmen."""
    if not windows:
        return []

    segments: list[dict] = []
    cur_emotion = windows[0]["emotion"]
    cur_start   = windows[0]["start"]
    cur_energies: list[float] = [windows[0]["energy"]]
    cur_pitches:  list[float] = [windows[0]["pitch_hz"]]
    cur_intensity = windows[0]["intensity"]

    for w in windows[1:]:
        if w["emotion"] == cur_emotion:
            cur_energies.append(w["energy"])
            cur_pitches.append(w["pitch_hz"])
            cur_intensity = max(cur_intensity, w["intensity"])
        else:
            dur = w["start"] - cur_start
            if dur >= min_dur and cur_emotion != "silence":
                segments.append(_make_segment(
                    cur_emotion, cur_start, w["start"],
                    cur_energies, cur_pitches, cur_intensity,
                ))
            cur_emotion  = w["emotion"]
            cur_start    = w["start"]
            cur_energies = [w["energy"]]
            cur_pitches  = [w["pitch_hz"]]
            cur_intensity = w["intensity"]

    # Flush terakhir
    if windows:
        last_end = windows[-1]["end"]
        dur = last_end - cur_start
        if dur >= min_dur and cur_emotion != "silence":
            segments.append(_make_segment(
                cur_emotion, cur_start, last_end,
                cur_energies, cur_pitches, cur_intensity,
            ))

    return segments


def _make_segment(
    emotion: str, start: float, end: float,
    energies: list[float], pitches: list[float],
    intensity: float,
) -> dict:
    valid_pitch = [p for p in pitches if p > 0]
    return {
        "start":       round(start, 2),
        "end":         round(end, 2),
        "emotion":     emotion,
        "avg_energy":  round(float(np.mean(energies)), 3),
        "avg_pitch":   round(float(np.mean(valid_pitch)) if valid_pitch else 0, 1),
        "intensity":   round(intensity, 3),
        "description": _emotion_description(emotion, float(np.mean(energies)), intensity),
    }


_EMOTION_DESC: dict[str, str] = {
    "laughing":  "Ketawa / momen lucu",
    "angry":     "Nada marah / tegas / intense",
    "excited":   "Semangat / excited",
    "sad":       "Sedih / melankolis / pelan",
    "neutral":   "Bicara normal",
    "silence":   "Hening",
}

def _emotion_description(emotion: str, energy: float, intensity: float) -> str:
    base = _EMOTION_DESC.get(emotion, emotion)
    if intensity > 0.80:
        return f"{base} (sangat kuat)"
    if intensity > 0.55:
        return f"{base} (kuat)"
    return base


# ---------------------------------------------------------------- summary

def _build_summary(windows: list[dict], segments: list[dict]) -> dict:
    if not windows:
        return {}

    # Dominant emotion (excluding silence)
    from collections import Counter
    emotion_counts = Counter(
        w["emotion"] for w in windows if w["emotion"] != "silence"
    )
    dominant = emotion_counts.most_common(1)[0][0] if emotion_counts else "neutral"

    avg_energy = round(float(np.mean([w["energy"] for w in windows])), 3)

    # Peak moments: segmen non-neutral dengan intensity tinggi
    peaks = sorted(
        [s for s in segments if s["emotion"] not in ("neutral", "silence") and s["intensity"] > 0.5],
        key=lambda s: s["intensity"], reverse=True,
    )[:8]

    return {
        "dominant_emotion": dominant,
        "avg_energy":       avg_energy,
        "peak_moments":     [
            {"time": p["start"], "emotion": p["emotion"], "intensity": p["intensity"]}
            for p in peaks
        ],
        "emotion_counts": dict(emotion_counts),
    }


# ---------------------------------------------------------------- helpers

def _extract_audio_wav(video_path: str) -> str:
    """Ekstrak audio dari video ke WAV 16kHz mono sementara."""
    fd, wav_path = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    r = subprocess.run([
        "ffmpeg", "-loglevel", "error",
        "-i", video_path,
        "-ar", str(_SR), "-ac", "1",  # 16kHz mono
        "-y", wav_path,
    ], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"FFmpeg audio extract gagal: {r.stderr[:200]}")
    return wav_path
