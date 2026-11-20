"""Efek berwaktu (expression effects). Grade warna kini resep dari Gemini/user (editing/renderer.grade_filter)."""

from __future__ import annotations

_EXPRESSIONS: dict[str, dict] = {
    "angry": {
        "label": "Angry", "emoji": "😡",
        "filters": [
            "colorchannelmixer=rr=1.5:gg=0.7:bb=0.7",
            "eq=saturation=1.3:brightness=0.03",
        ],
    },
    "crying": {
        "label": "Crying", "emoji": "😢",
        "filters": [
            "colorchannelmixer=rr=0.7:gg=0.85:bb=1.4",
            "eq=saturation=0.6:brightness=-0.04",
        ],
    },
    "shocked": {
        "label": "Shocked", "emoji": "😱",
        "filters": ["eq=contrast=1.6:saturation=0.15:brightness=0.08"],
    },
    "laughing": {
        "label": "Laughing", "emoji": "😂",
        "filters": [
            "colorchannelmixer=rr=1.1:gg=1.0:bb=0.6",
            "eq=saturation=1.5:brightness=0.07:contrast=1.1",
        ],
    },
    "dreamy": {
        "label": "Dreamy", "emoji": "✨",
        "filters": [
            "gblur=sigma=3",
            "eq=saturation=0.7:brightness=0.05",
        ],
    },
    "dramatic": {
        "label": "Dramatic", "emoji": "🎭",
        "filters": [
            "eq=saturation=0.1:contrast=1.4:brightness=-0.05",
            "vignette=angle=PI/4",
        ],
    },
}


def get_expressions() -> list[dict]:
    return [{"id": k, "label": v["label"], "emoji": v["emoji"]} for k, v in _EXPRESSIONS.items()]


def _build_expression_vf(expressions: list[dict]) -> list[str]:
    parts = []
    for expr in expressions:
        cfg = _EXPRESSIONS.get(expr.get("type", ""))
        if not cfg:
            continue
        s, e = float(expr.get("start", 0)), float(expr.get("end", 0))
        if e <= s:
            continue
        gate = f"between(t,{s},{e})"
        for f_str in cfg["filters"]:
            parts.append(f"{f_str}:enable='{gate}'")
    return parts
