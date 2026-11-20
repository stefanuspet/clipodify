"""Persona channel + playbook (YAML di folder `personas/`).

Persona: panduan gaya untuk Gemini + `limits` yang dipaksakan validator.
Playbook: resep edit per intent × beat.
"""

from __future__ import annotations

import hashlib
import json
import os

import yaml

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
PERSONA_DIR = os.path.join(_ROOT, "personas")
DEFAULT_PERSONA = os.getenv("EDIT_PERSONA", "stefra_default")

DEFAULT_LIMITS = {
    "max_sfx_per_10s": 2,
    "min_sfx_gap_s": 1.5,
    "max_text_overlays": 3,
    "max_effects": 3,
    "highlight_every_n_words": 8,
    "no_sfx_in_beats": ["laugh", "emotional_peak"],
    "max_zoom_per_10s": 1,
    "max_cut_ratio": 0.30,
    "transition_sfx": True,          # whoosh otomatis saat layout grid berganti
    "transition_sfx_volume": 0.45,
    "max_build_ups": 2,
    "fill_gaps": True,               # 8A-4: isi celah panjang otomatis
    "max_gap_s": 6.0,
    "transition_visual": "zoom",      # 8D-1: zoom | flash | mix | none
    "transition_zoom": 1.06,
    "max_emojis": 3,                  # 8D-2
    "name_labels": True,              # 8D-3
    "progress_bar": True,             # 8D-4
    "music_follow_beats": True,       # 8E-2
}


def _read_yaml(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_persona(persona_id: str | None = None) -> dict:
    pid = persona_id or DEFAULT_PERSONA
    path = os.path.join(PERSONA_DIR, f"{pid}.yaml")
    persona = _read_yaml(path)
    persona.setdefault("id", pid)
    persona["limits"] = {**DEFAULT_LIMITS, **(persona.get("limits") or {})}
    pal = (persona.get("style") or {}).get("palette") or {}
    persona["limits"].setdefault("palette", pal)
    persona["limits"].setdefault("progress_color", pal.get("highlight", "#FFE000"))
    raw = json.dumps(persona, sort_keys=True, ensure_ascii=False).encode()
    persona["_version"] = hashlib.sha1(raw).hexdigest()[:8]
    return persona


def load_playbook() -> dict:
    return _read_yaml(os.path.join(PERSONA_DIR, "playbook.yaml"))


def beat_types() -> list[str]:
    return list((load_playbook().get("beat_types") or {}).keys())


def persona_prompt(p: dict) -> str:
    st = p.get("style") or {}
    lim = p["limits"]
    lines = [
        f"Nama: {p.get('name', p['id'])}",
        f"Penonton: {p.get('audience', '-')}",
        f"Gaya bahasa teks: {p.get('voice', '-')}; {st.get('text_tone', '')}",
        f"Tujuan: {p.get('goal', '-')}",
        f"Tempo: {p.get('pace', 'medium')}; kepadatan SFX: {st.get('sfx_density', 'medium')}",
    ]
    if st.get("subtitle"):
        lines.append(f"Subtitle: {st['subtitle']}")
    if st.get("palette"):
        lines.append("Palet: " + ", ".join(f"{k} {v}" for k, v in st["palette"].items()))
    if st.get("grade"):
        lines.append(f"Grade: {st['grade']}")
    if st.get("music"):
        lines.append(f"Musik: {st['music']}")
    if st.get("cuts"):
        lines.append(f"Potongan: {st['cuts']}")
    for item in p.get("always") or []:
        lines.append(f"SELALU: {item}")
    for item in p.get("never") or []:
        lines.append(f"JANGAN: {item}")
    lines.append(
        f"Batas pasti (validator akan membuang pelanggaran): SFX maks {lim['max_sfx_per_10s']} per "
        f"10 detik dengan jarak ≥{lim['min_sfx_gap_s']} detik; teks overlay maks "
        f"{lim['max_text_overlays']}; efek maks {lim['max_effects']}; highlight maks 1 per "
        f"{lim['highlight_every_n_words']} kata; tidak ada SFX di beat: "
        f"{', '.join(lim['no_sfx_in_beats']) or '-'}; zoom maks {lim['max_zoom_per_10s']} per 10 "
        f"detik; total potongan maks {int(lim['max_cut_ratio'] * 100)}% durasi."
    )
    return "\n".join(lines)


def playbook_prompt(pb: dict) -> str:
    lines = ["Jenis beat:"]
    lines += [f"- {k}: {v}" for k, v in (pb.get("beat_types") or {}).items()]
    lines.append("\nResep per intent:")
    for intent, r in (pb.get("intents") or {}).items():
        lines.append(f"[{intent}] subtitle: {r.get('subtitle', '-')}; grade: {r.get('grade', '-')}")
        for beat, how in (r.get("beats") or {}).items():
            lines.append(f"  - {beat}: {how}")
        for key, label in (("text", "teks"), ("zoom", "zoom"), ("music", "musik"),
                           ("cuts", "potongan"), ("hook", "hook")):
            if r.get(key):
                lines.append(f"  - {label}: {r[key]}")
    return "\n".join(lines)


def examples_prompt(p: dict, max_examples: int = 2) -> str:
    """Plan contoh (yang terbukti bagus) dalam bentuk ringkas untuk few-shot."""
    out = []
    for path in (p.get("examples") or [])[:max_examples]:
        full = path if os.path.isabs(path) else os.path.join(_ROOT, path)
        if not os.path.exists(full):
            continue
        with open(full, encoding="utf-8") as f:
            rec = json.load(f)
        raw = rec.get("raw_plan") or {}
        keep = {k: raw.get(k) for k in ("intent", "beats", "subtitle", "grade", "sfx",
                                        "text_overlays", "effects")}
        out.append(json.dumps(keep, ensure_ascii=False))
    return "\n".join(out)
