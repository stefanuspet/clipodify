"""Skema EditPlan v1 + katalog aset yang boleh dipilih Gemini.

Semua waktu di plan ditambatkan ke nomor kata (dari context pack), bukan detik mentah.
Validator yang mengubahnya ke detik.
"""

from __future__ import annotations

INTENTS = ["funny", "motivation", "insight", "story", "debate", "rant", "reveal", "emotional", "quotable"]
FRAMINGS = ["grid_auto", "center_crop"]
POSITIONS = ["top", "center", "bottom"]
TEXT_ROLES = ["hook_title", "emphasis", "question", "cta"]
ZOOM_STYLES = ["punch", "slow"]
PERSON_ROLES = ["host", "guest", "co_host", "audience", "unknown"]

def catalog() -> dict:
    """Aset yang tersedia saat ini (dibaca dari kode/disk, bukan hardcode ganda)."""
    from backend.editing.fonts import curated
    from backend.editing.subtitle_style import list_styles
    from backend.renderer.filters import _EXPRESSIONS
    from backend.renderer.sfx import list_sfx
    from backend.editing.persona import beat_types
    from backend.editing.music import MOODS, available_moods
    sfx = list_sfx()
    tags: dict[str, dict] = {}
    for x in sfx:
        tags.setdefault(x["tag"], {"tag": x["tag"], "use": x["use"], "files": []})["files"].append(x["id"])
    return {
        "beat_types": beat_types(),
        "sfx_tags": list(tags.values()),
        "music_moods": MOODS,
        "music_available": available_moods(),
        "font_families": curated(40),
        "styles": list_styles(),
        "effects": list(_EXPRESSIONS.keys()),
        "sfx": [{"id": s["id"], "category": s["category"], "duration_s": s["duration_s"],
                 "tag": s["tag"], "length": s.get("length", ""), "peak_s": s.get("peak_s", 0.0)}
                for s in sfx],
    }


def recipe_schema(cat: dict) -> dict:
    from backend.editing.subtitle_style import ACTIVE_MODES, ANIM_TYPES, CASES, COLOR_MODES, POSITIONS as SP, WEIGHTS
    s = lambda **kw: {"type": "STRING", **kw}  # noqa: E731
    n = {"type": "NUMBER"}
    obj = lambda props: {"type": "OBJECT", "properties": props, "required": list(props)}  # noqa: E731
    return obj({
        "name": s(),
        "font": obj({"family": s(enum=[f["family"] for f in cat["font_families"]]), "weight": s(enum=WEIGHTS)}),
        "size": n, "case": s(enum=CASES), "letter_spacing": n, "words_per_line": {"type": "INTEGER"},
        "color_mode": s(enum=COLOR_MODES), "fill_colors": {"type": "ARRAY", "items": s()},
        "outline": obj({"color": s(), "width": n}),
        "shadow": obj({"color": s(), "opacity": n, "distance": n}),
        "box": obj({"enabled": {"type": "BOOLEAN"}, "color": s(), "opacity": n, "padding": n}),
        "active_word": obj({"mode": s(enum=ACTIVE_MODES), "color": s(), "scale": n}),
        "highlight": obj({"color": s(), "scale": n}),
        "animation": obj({"type": s(enum=ANIM_TYPES), "from_scale": n, "overshoot": n, "duration_ms": n}),
        "position": s(enum=SP),
    })


def response_schema(cat: dict, person_ids: list[str] | None = None) -> dict:
    from backend.editing.visuals import EMOJI_SET
    s = lambda **kw: {"type": "STRING", **kw}  # noqa: E731
    n = {"type": "NUMBER"}
    i = {"type": "INTEGER"}
    return {
        "type": "OBJECT",
        "properties": {
            "people": {
                "type": "ARRAY",
                "items": {
                    "type": "OBJECT",
                    "properties": {
                        "id": s(enum=person_ids or ["none"]),
                        "name": s(),
                        "role": s(enum=PERSON_ROLES),
                        "confidence": n,
                        "evidence": s(),
                    },
                    "required": ["id", "name", "role", "confidence", "evidence"],
                },
            },
            "beats": {
                "type": "ARRAY",
                "items": {
                    "type": "OBJECT",
                    "properties": {
                        "type": s(enum=cat["beat_types"]),
                        "from_word": i,
                        "to_word": i,
                        "note": s(),
                    },
                    "required": ["type", "from_word", "to_word", "note"],
                },
            },
            "intent": s(enum=INTENTS),
            "reasoning": s(),
            "cuts": {
                "type": "OBJECT",
                "properties": {
                    "remove_silence": {"type": "BOOLEAN"},
                    "remove_fillers": {"type": "BOOLEAN"},
                    "ranges": {
                        "type": "ARRAY",
                        "items": {
                            "type": "OBJECT",
                            "properties": {"from_word": i, "to_word": i, "why": s()},
                            "required": ["from_word", "to_word", "why"],
                        },
                    },
                },
                "required": ["remove_silence", "remove_fillers", "ranges"],
            },
            "hook_teaser": {
                "type": "OBJECT",
                "properties": {"enabled": {"type": "BOOLEAN"}, "from_word": i, "to_word": i, "why": s()},
                "required": ["enabled", "from_word", "to_word", "why"],
            },
            "emojis": {
                "type": "ARRAY",
                "items": {
                    "type": "OBJECT",
                    "properties": {"word_index": i, "emoji": s(enum=EMOJI_SET), "why": s()},
                    "required": ["word_index", "emoji", "why"],
                },
            },
            "build_ups": {
                "type": "ARRAY",
                "items": {
                    "type": "OBJECT",
                    "properties": {"to_word": i, "length": s(enum=["short", "long"]),
                                   "hit": s(enum=["impact", "bass_drop", "none"]), "why": s()},
                    "required": ["to_word", "length", "hit", "why"],
                },
            },
            "zooms": {
                "type": "ARRAY",
                "items": {
                    "type": "OBJECT",
                    "properties": {"from_word": i, "to_word": i, "scale": n,
                                   "style": s(enum=ZOOM_STYLES), "why": s()},
                    "required": ["from_word", "to_word", "scale", "style", "why"],
                },
            },
            "music": {
                "type": "OBJECT",
                "properties": {"enabled": {"type": "BOOLEAN"}, "mood": s(enum=cat["music_moods"]),
                               "energy": s(enum=["low", "mid", "high"]), "volume_db": n},
                "required": ["enabled", "mood", "energy", "volume_db"],
            },
            "end_card": {
                "type": "OBJECT",
                "properties": {"enabled": {"type": "BOOLEAN"}, "text": s(), "duration_s": n},
                "required": ["enabled", "text", "duration_s"],
            },
            "framing": s(enum=FRAMINGS),
            "subtitle": {
                "type": "OBJECT",
                "properties": {
                    "enabled": {"type": "BOOLEAN"},
                    "base_style_id": s(enum=[x["id"] for x in cat["styles"]] + ["baru"]),
                    "recipe": recipe_schema(cat),
                    "highlight_words": {"type": "ARRAY", "items": i},
                },
                "required": ["enabled", "base_style_id", "recipe", "highlight_words"],
            },
            "grade": {
                "type": "OBJECT",
                "properties": {
                    "contrast": n, "saturation": n, "brightness": n, "gamma": n, "warmth": n, "tint": n,
                },
                "required": ["contrast", "saturation", "brightness", "gamma", "warmth", "tint"],
            },
            "sfx": {
                "type": "ARRAY",
                "items": {
                    "type": "OBJECT",
                    "properties": {
                        "tag": s(enum=[x["tag"] for x in cat["sfx_tags"] if x["tag"] != "riser"] or ["none"]),
                        "word_index": i,
                        "edge": s(enum=["start", "end"]),
                        "offset_s": n,
                        "volume": n,
                        "why": s(),
                    },
                    "required": ["tag", "word_index", "edge", "offset_s", "volume", "why"],
                },
            },
            "text_overlays": {
                "type": "ARRAY",
                "items": {
                    "type": "OBJECT",
                    "properties": {
                        "text": s(),
                        "role": s(enum=TEXT_ROLES),
                        "start_word": i,
                        "duration_s": n,
                        "position": s(enum=POSITIONS),
                        "color": s(),
                        "font": s(enum=[f["family"] for f in cat["font_families"]]),
                    },
                    "required": ["text", "role", "start_word", "duration_s", "position",
                                 "color", "font"],
                },
            },
            "effects": {
                "type": "ARRAY",
                "items": {
                    "type": "OBJECT",
                    "properties": {
                        "type": s(enum=cat["effects"]),
                        "from_word": i,
                        "to_word": i,
                    },
                    "required": ["type", "from_word", "to_word"],
                },
            },
        },
        "required": ["people", "beats", "intent", "reasoning", "cuts", "hook_teaser", "framing", "subtitle",
                     "grade", "build_ups", "emojis", "zooms", "sfx", "text_overlays", "effects", "music", "end_card"],
        # Beat ditulis lebih dulu: Gemini menganalisis momen, baru memilih edit.
        "property_ordering": ["people", "beats", "intent", "reasoning", "cuts", "hook_teaser", "framing",
                              "subtitle", "grade", "build_ups", "emojis", "zooms", "sfx", "text_overlays", "effects",
                              "music", "end_card"],
    }
