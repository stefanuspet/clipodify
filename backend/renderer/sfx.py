"""SFX operations — list, mix, path resolution."""

from __future__ import annotations

import os
import subprocess

_SFX_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "assets", "sfx")

_SFX_META: dict[str, dict] = {
    "sfx_whoosh":  {"name": "Whoosh",  "category": "transition"},
    "sfx_swoosh":  {"name": "Swoosh",  "category": "transition"},
    "sfx_pop":     {"name": "Pop",     "category": "emphasis"},
    "sfx_tap":     {"name": "Tap",     "category": "emphasis"},
    "sfx_impact":  {"name": "Impact",  "category": "impact"},
    "sfx_ding":    {"name": "Ding",    "category": "tone"},
    "sfx_rise":    {"name": "Rise",    "category": "buildup"},
    "sfx_glitch":  {"name": "Glitch",  "category": "effect"},
}


def _load_meta() -> dict[str, dict]:
    """Gabung metadata bawaan dengan assets/sfx/sfx_meta.yaml (tag, kegunaan, sumber)."""
    meta = {k: dict(v) for k, v in _SFX_META.items()}
    path = os.path.join(_SFX_DIR, "sfx_meta.yaml")
    if os.path.exists(path):
        import yaml
        with open(path, encoding="utf-8") as f:
            for k, v in (yaml.safe_load(f) or {}).items():
                meta.setdefault(k, {}).update(v or {})
    return meta


def _sfx_duration(path: str) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", path],
        capture_output=True, text=True,
    )
    try:
        return round(float(r.stdout.strip()), 2)
    except Exception:
        return 0.0


_PEAK_CACHE: dict[tuple[str, float], float] = {}


def _sfx_peak(path: str) -> float:
    """Detik di dalam file tempat bunyinya paling keras (untuk menyelaraskan SFX ke momen)."""
    key = (path, os.path.getmtime(path))
    if key not in _PEAK_CACHE:
        try:
            import librosa
            import numpy as np
            y, sr = librosa.load(path, sr=22050, mono=True)
            w = max(1, int(sr * 0.02))
            _PEAK_CACHE[key] = round(float(np.convolve(y ** 2, np.ones(w), "same").argmax()) / sr, 3)
        except Exception:
            _PEAK_CACHE[key] = 0.0
    return _PEAK_CACHE[key]


# 8B-3: semua SFX disamakan ke loudness puncak (momentary max, EBU R128) yang sama.
# File asli tidak diubah; renderer mengalikan volume dengan norm_gain.
TARGET_LUFS = -16.5
MAX_TRUE_PEAK = -1.0
MAX_GAIN_DB = 40.0
LIMITER_MAX_DB = 9.0          # limiter boleh memangkas puncak sampai sekian dB (bunyi transien)
_LOUD_CACHE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "tmp", "sfx_loudness.json")


def _measure_loudness(path: str) -> tuple[float, float]:
    """(loudness momentary maks LUFS, true peak dBTP) via ffmpeg ebur128."""
    import re
    r = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-v", "verbose", "-i", path, "-af",
                        "apad=pad_dur=0.5,ebur128=peak=true:framelog=verbose", "-f", "null", "-"],
                       capture_output=True, text=True).stderr
    ms = [float(x) for x in re.findall(r"\sM:\s*(-?[\d.]+)", r)]
    pk = re.findall(r"Peak:\s*(-?[\d.]+|-inf)", r)
    m = max(ms) if ms else -70.0
    p = float(pk[-1]) if pk and pk[-1] != "-inf" else -70.0
    return m, p


def sfx_loudness(path: str) -> dict:
    import json
    key = f"{os.path.basename(path)}|{os.path.getmtime(path):.0f}|{os.path.getsize(path)}"
    cache = {}
    if os.path.exists(_LOUD_CACHE):
        try:
            with open(_LOUD_CACHE, encoding="utf-8") as f:
                cache = json.load(f)
        except (OSError, ValueError):
            cache = {}
    if key not in cache:
        m, p = _measure_loudness(path)
        want = min(TARGET_LUFS - m, MAX_GAIN_DB)
        free = MAX_TRUE_PEAK - p                    # naik sebanyak ini masih aman tanpa limiter
        limit = want > free                          # transien: butuh limiter untuk mencapai target
        gain = min(want, free + LIMITER_MAX_DB) if limit else want
        cache[key] = {"lufs": round(m, 1), "true_peak": round(p, 1), "gain_db": round(gain, 1),
                      "limiter": limit}
        os.makedirs(os.path.dirname(_LOUD_CACHE), exist_ok=True)
        with open(_LOUD_CACHE, "w", encoding="utf-8") as f:
            json.dump(cache, f, indent=1)
    return cache[key]


def norm_gain(sfx_id: str) -> float:
    """Pengali linear untuk menyamakan SFX ini ke TARGET_LUFS (1.0 bila tidak ditemukan)."""
    path = _sfx_path(sfx_id)
    return 10 ** (sfx_loudness(path)["gain_db"] / 20) if path else 1.0


def norm_filter(sfx_id: str, volume: float) -> str:
    """Filter ffmpeg: penyamaan level × volume pilihan, + limiter bila bunyinya transien."""
    path = _sfx_path(sfx_id)
    info = sfx_loudness(path) if path else {"gain_db": 0.0, "limiter": False}
    f = f"volume={info['gain_db']:.1f}dB,volume={volume:.3f}"
    if info.get("limiter"):
        lim = 10 ** (MAX_TRUE_PEAK / 20)
        f = f"volume={info['gain_db']:.1f}dB,alimiter=limit={lim:.3f}:attack=0.5:release=40:level=disabled,volume={volume:.3f}"
    return f


def list_sfx() -> list[dict]:
    if not os.path.isdir(_SFX_DIR):
        return []
    result = []
    all_meta = _load_meta()
    for name in sorted(os.listdir(_SFX_DIR)):
        if name.lower().endswith((".mp3", ".wav", ".ogg", ".m4a")):
            path = os.path.join(_SFX_DIR, name)
            sfx_id = os.path.splitext(name)[0]
            meta = all_meta.get(sfx_id, {})
            display_name = meta.get("name") or sfx_id.replace("sfx_", "").replace("_", " ").title()
            result.append({
                "id":         sfx_id,
                "name":       display_name,
                "filename":   name,
                "path":       path,
                "category":   meta.get("category", "other"),
                "tag":        meta.get("tag") or sfx_id.replace("sfx_", ""),
                "use":        meta.get("use", ""),
                "intensity":  meta.get("intensity", "mid"),
                "duration_s": _sfx_duration(path),
                "peak_s":     _sfx_peak(path),
                "loudness":   sfx_loudness(path),
                "align":      meta.get("align", "peak"),    # "end" = akhir file jatuh di momen (riser)
                "length":     meta.get("length", ""),
                "size_kb":    round(os.path.getsize(path) / 1024, 1),
            })
    return result


def _sfx_path(sfx_id: str) -> str | None:
    for sfx in list_sfx():
        if sfx["id"] == sfx_id:
            return sfx["path"]
    return None


def mix_sfx(video_path: str, sfx_items: list[dict], output_path: str) -> str:
    """Overlay SFX ke audio track video. Returns path to output file."""
    from .core import _ffmpeg  # avoid circular at module level

    sfx_out = output_path.replace(".mp4", "_sfx.mp4")
    inputs = ["-i", video_path]
    filter_parts = ["[0:a]aformat=sample_rates=44100:channel_layouts=stereo[base]"]
    mix_inputs = ["[base]"]

    valid_sfx = []
    for item in sfx_items:
        path = _sfx_path(item.get("sfx_id", ""))
        if path and os.path.exists(path):
            valid_sfx.append((path, item))

    if not valid_sfx:
        return video_path

    for i, (path, item) in enumerate(valid_sfx):
        inputs += ["-i", path]
        idx = i + 1
        ts = item.get("timestamp", 0)
        vol = item.get("volume", 1.0)
        filter_parts.append(
            f"[{idx}:a]adelay={int(ts * 1000)}|{int(ts * 1000)},volume={vol}[sfx{i}]"
        )
        mix_inputs.append(f"[sfx{i}]")

    n = len(mix_inputs)
    filter_parts.append(f"{''.join(mix_inputs)}amix=inputs={n}:normalize=0[aout]")
    filter_complex = ";".join(filter_parts)

    _ffmpeg([
        *inputs,
        "-filter_complex", filter_complex,
        "-map", "0:v",
        "-map", "[aout]",
        "-c:v", "copy",
        "-y", sfx_out,
    ])
    return sfx_out
