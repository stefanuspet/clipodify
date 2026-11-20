"""Library musik latar (assets/music/music_meta.yaml). Hanya lagu berlisensi jelas yang dipakai."""

from __future__ import annotations

import os
import random

import yaml

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
MUSIC_DIR = os.path.join(_ROOT, "assets", "music")
META_PATH = os.path.join(MUSIC_DIR, "music_meta.yaml")

MOODS = ["playful", "upbeat", "chill", "inspirational", "cinematic", "tense", "sad", "ambient"]
ENERGIES = ["low", "mid", "high"]


def _load() -> list[dict]:
    if not os.path.exists(META_PATH):
        return []
    with open(META_PATH, encoding="utf-8") as f:
        return (yaml.safe_load(f) or {}).get("tracks") or []


def usable_tracks() -> list[dict]:
    """Lagu yang filenya ada DAN lisensinya tercatat (source + type)."""
    out = []
    for t in _load():
        lic = t.get("license") or {}
        path = os.path.join(MUSIC_DIR, t.get("file", ""))
        if t.get("file") and os.path.exists(path) and lic.get("source") and lic.get("type"):
            out.append({**t, "path": path})
    return out


def available_moods() -> list[str]:
    moods = {m for t in usable_tracks() for m in (t.get("mood") or [])}
    return [m for m in MOODS if m in moods]


def pick_track(mood: str, energy: str, seed: str) -> dict | None:
    tracks = usable_tracks()
    if not tracks:
        return None

    def score(t):
        return (2 if mood in (t.get("mood") or []) else 0) + (1 if t.get("energy") == energy else 0)

    best = max(score(t) for t in tracks)
    pool = [t for t in tracks if score(t) == best]
    return random.Random(seed).choice(pool)


def credit(track: dict) -> str | None:
    lic = track.get("license") or {}
    if lic.get("attribution_required"):
        return lic.get("attribution_text") or f"Musik: {track.get('title', track['file'])} — {lic['source']}"
    return None


# ------------------------------------------------------------------ 8E-2 musik mengikuti momen

_BUILD_GAIN_DB = 4.0
_DROP_DB = -30.0
_DROP_BEFORE_S = 0.45
_HIT_BOOST_DB = 3.0
_HIT_DECAY_S = 1.5
_EMO_DIP_DB = -4.0
_END_BOOST_DB = 3.0


def music_automation(plan: dict, tl, base_db: float) -> tuple[str, list[dict]]:
    """Kurva volume musik (waktu hasil) sebagai ekspresi ffmpeg `volume` + daftar momen.

    Di atas ducking otomatis: naik di beat build, berhenti sebelum build-up lalu masuk lagi
    di hit, turun di puncak emosi, naik di end card.
    """
    terms, moments = [], []
    if plan.get("music_follow_beats", True):
        for b in plan.get("beats") or []:
            a, e = tl.map_time(b["s"]), tl.map_time(b["e"])
            if e - a < 0.8:
                continue
            if b["type"] == "build":
                terms.append(f"if(between(t,{a:.3f},{e:.3f}),{_BUILD_GAIN_DB}*(t-{a:.3f})/{e - a:.3f},"
                             f"if(between(t,{e:.3f},{e + 0.5:.3f}),{_BUILD_GAIN_DB}*(1-(t-{e:.3f})/0.5),0))")
                moments.append({"t": round(a, 2), "end": round(e, 2), "kind": "naik (build)"})
            elif b["type"] == "emotional_peak":
                terms.append(f"if(between(t,{a:.3f},{e:.3f}),{_EMO_DIP_DB},0)")
                moments.append({"t": round(a, 2), "end": round(e, 2), "kind": "turun (haru)"})
        for bu in plan.get("build_ups") or []:
            c = tl.map_time(bu["s"])
            d0 = c - _DROP_BEFORE_S
            terms.append(f"if(between(t,{d0:.3f},{c:.3f}),{_DROP_DB}*min(1,(t-{d0:.3f})/0.08),0)")
            terms.append(f"if(between(t,{c:.3f},{c + _HIT_DECAY_S:.3f}),{_HIT_BOOST_DB}*(1-(t-{c:.3f})/{_HIT_DECAY_S}),0)")
            moments.append({"t": round(d0, 2), "end": round(c, 2), "kind": "berhenti → hit"})
    if tl.end_card_s:
        terms.append(f"if(gte(t,{tl.duration:.3f}),{_END_BOOST_DB},0)")
        moments.append({"t": round(tl.duration, 2), "end": round(tl.total, 2), "kind": "naik (end card)"})
    db = f"{base_db:.1f}" + "".join(f"+{x}" for x in terms)
    return f"volume='pow(10,({db})/20)':eval=frame", moments
