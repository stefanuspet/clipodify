"""Daftarkan file musik baru di assets/music ke music_meta.yaml.

Mengisi durasi, bpm, dan perkiraan energi otomatis. `mood` dan `license` WAJIB
dilengkapi manual — lagu tanpa lisensi tidak akan dipakai renderer.

Jalankan: python tools/index_music.py
"""

from __future__ import annotations

import os
import sys

import librosa
import numpy as np
import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from backend.editing.music import META_PATH, MUSIC_DIR  # noqa: E402

AUDIO_EXT = (".mp3", ".wav", ".ogg", ".m4a", ".flac")


def analyze(path: str) -> dict:
    y, sr = librosa.load(path, sr=22050, mono=True)
    tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
    rms = float(np.mean(librosa.feature.rms(y=y)))
    energy = "low" if rms < 0.06 else "mid" if rms < 0.14 else "high"
    return {"bpm": int(round(float(np.atleast_1d(tempo)[0]))), "duration_s": round(len(y) / sr, 1),
            "energy": energy}


def main() -> None:
    data, header = {}, ""
    if os.path.exists(META_PATH):
        with open(META_PATH, encoding="utf-8") as f:
            text = f.read()
        data = yaml.safe_load(text) or {}
        # Pertahankan komentar panduan di atas `tracks:`
        header = text.split("\ntracks:", 1)[0] + "\n" if "\ntracks:" in text else ""
    tracks = data.get("tracks") or []
    known = {t.get("file") for t in tracks}
    added = 0
    for name in sorted(os.listdir(MUSIC_DIR)):
        if not name.lower().endswith(AUDIO_EXT) or name in known:
            continue
        info = analyze(os.path.join(MUSIC_DIR, name))
        tracks.append({
            "file": name, "title": os.path.splitext(name)[0], "mood": [], **info,
            "license": {"source": "", "url": "", "type": "", "attribution_required": False,
                        "attribution_text": ""},
        })
        added += 1
        print(f"+ {name}: {info}  → lengkapi mood & license!")
    data["tracks"] = tracks
    with open(META_PATH, "w", encoding="utf-8") as f:
        f.write(header)
        yaml.safe_dump({"tracks": tracks}, f, allow_unicode=True, sort_keys=False)
    print(f"{added} lagu baru didaftarkan, total {len(tracks)}.")


if __name__ == "__main__":
    main()
