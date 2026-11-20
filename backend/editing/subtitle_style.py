"""Resep gaya subtitle (aset di assets/subtitle_styles/*.json) + builder ASS generiknya.

Resep dirancang dari "bahan dasar" (font, warna, outline, animasi, …) — dibuat user di
Studio Subtitle atau oleh Gemini. Validator menjaga supaya hasilnya selalu terbaca.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from datetime import datetime

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
STYLE_DIR = os.path.join(_ROOT, "assets", "subtitle_styles")
TRASH_DIR = os.path.join(STYLE_DIR, ".trash")

ANIM_TYPES = ["pop", "bounce", "slide_up", "fade", "wipe", "none"]
ACTIVE_MODES = ["none", "color", "scale", "chip"]
COLOR_MODES = ["single", "cycle_word", "cycle_line"]
CASES = ["upper", "lower", "asis"]
POSITIONS = ["top", "center", "bottom"]
WEIGHTS = ["400", "500", "600", "700", "800", "900"]

DEFAULT_RECIPE: dict = {
    "name": "Default",
    "font": {"family": "Anton", "weight": "400"},
    "size": 105,
    "case": "upper",
    "letter_spacing": 0,
    "words_per_line": 3,
    "color_mode": "single",
    "fill_colors": ["#FFFFFF"],
    "outline": {"color": "#000000", "width": 0.12},
    "shadow": {"color": "#000000", "opacity": 0.5, "distance": 0.03},
    "box": {"enabled": False, "color": "#000000", "opacity": 0.65, "padding": 0.18},
    "active_word": {"mode": "color", "color": "#FFE600", "scale": 1.12},
    "highlight": {"color": "#FFE600", "scale": 1.15},
    "animation": {"type": "pop", "from_scale": 60, "overshoot": 112, "duration_ms": 180},
    "position": "bottom",
}

_HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")


# ------------------------------------------------------------------ validasi

def _num(v, lo, hi, default):
    try:
        return max(lo, min(hi, float(v)))
    except (TypeError, ValueError):
        return default


def _hex(v, default, fixes, field):
    if isinstance(v, str) and _HEX.match(v.strip()):
        return v.strip().upper()
    if v is not None:
        fixes.append(f"{field}: warna {v!r} tidak valid → {default}")
    return default


def _luma(hex_color: str) -> float:
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def validate_recipe(r: dict | None, fixes: list[str] | None = None, prefix: str = "subtitle") -> dict:
    """Lengkapi + jepit resep. Selalu menghasilkan resep yang bisa dirender & terbaca."""
    fixes = fixes if fixes is not None else []
    r = r or {}
    d = DEFAULT_RECIPE
    font = r.get("font") or {}
    out = {
        "name": str(r.get("name") or "Tanpa nama")[:60],
        "font": {"family": str(font.get("family") or d["font"]["family"])[:60],
                 "weight": str(font.get("weight")) if str(font.get("weight")) in WEIGHTS else "400"},
        "size": int(_num(r.get("size"), 60, 170, d["size"])),
        "case": r.get("case") if r.get("case") in CASES else "upper",
        "letter_spacing": int(_num(r.get("letter_spacing"), -4, 20, 0)),
        "words_per_line": int(_num(r.get("words_per_line"), 1, 5, d["words_per_line"])),
        "color_mode": r.get("color_mode") if r.get("color_mode") in COLOR_MODES else "single",
    }
    colors = [_hex(c, None, fixes, f"{prefix}.fill_colors") for c in (r.get("fill_colors") or [])][:6]
    out["fill_colors"] = [c for c in colors if c] or ["#FFFFFF"]

    o, s, b = r.get("outline") or {}, r.get("shadow") or {}, r.get("box") or {}
    out["outline"] = {"color": _hex(o.get("color"), "#000000", fixes, f"{prefix}.outline.color"),
                      "width": round(_num(o.get("width"), 0, 0.35, d["outline"]["width"]), 3)}
    out["shadow"] = {"color": _hex(s.get("color"), "#000000", fixes, f"{prefix}.shadow.color"),
                     "opacity": round(_num(s.get("opacity"), 0, 1, 0.5), 2),
                     "distance": round(_num(s.get("distance"), 0, 0.12, 0.03), 3)}
    out["box"] = {"enabled": bool(b.get("enabled")),
                  "color": _hex(b.get("color"), "#000000", fixes, f"{prefix}.box.color"),
                  "opacity": round(_num(b.get("opacity"), 0.2, 1, 0.65), 2),
                  "padding": round(_num(b.get("padding"), 0.05, 0.4, 0.18), 3)}

    a, aw, hl = r.get("animation") or {}, r.get("active_word") or {}, r.get("highlight") or {}
    out["active_word"] = {"mode": aw.get("mode") if aw.get("mode") in ACTIVE_MODES else "none",
                          "color": _hex(aw.get("color"), "#FFE600", fixes, f"{prefix}.active_word.color"),
                          "scale": round(_num(aw.get("scale"), 1.0, 1.4, 1.12), 2)}
    out["highlight"] = {"color": _hex(hl.get("color"), "#FFE600", fixes, f"{prefix}.highlight.color"),
                        "scale": round(_num(hl.get("scale"), 1.0, 1.4, 1.15), 2)}
    out["animation"] = {"type": a.get("type") if a.get("type") in ANIM_TYPES else "pop",
                        "from_scale": int(_num(a.get("from_scale"), 0, 100, 60)),
                        "overshoot": int(_num(a.get("overshoot"), 100, 140, 112)),
                        "duration_ms": int(_num(a.get("duration_ms"), 60, 450, 180))}
    out["position"] = r.get("position") if r.get("position") in POSITIONS else "bottom"

    # Keterbacaan: teks terang/gelap tanpa outline, bayangan, atau kotak akan tenggelam di video
    if not out["box"]["enabled"] and out["outline"]["width"] < 0.05 and out["shadow"]["opacity"] < 0.4:
        fixes.append(f"{prefix}: tanpa outline/bayangan/kotak teks sulit terbaca → outline 0.08")
        out["outline"]["width"] = 0.08
    if not out["box"]["enabled"] and out["outline"]["width"] >= 0.05:
        ol = _luma(out["outline"]["color"])
        if any(abs(_luma(c) - ol) < 60 for c in out["fill_colors"]):
            fixes.append(f"{prefix}: warna isi terlalu mirip outline → outline hitam/putih")
            out["outline"]["color"] = "#000000" if all(_luma(c) > 128 for c in out["fill_colors"]) else "#FFFFFF"
    return out


def recipe_hash(recipe: dict) -> str:
    core = {k: v for k, v in recipe.items() if k != "name"}
    return hashlib.sha1(json.dumps(core, sort_keys=True).encode()).hexdigest()[:8]


# ------------------------------------------------------------------ penyimpanan (aset)

def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")[:40] or "gaya"


def list_styles() -> list[dict]:
    os.makedirs(STYLE_DIR, exist_ok=True)
    out = []
    for name in sorted(os.listdir(STYLE_DIR)):
        if name.endswith(".json"):
            with open(os.path.join(STYLE_DIR, name), encoding="utf-8") as f:
                out.append(json.load(f))
    out.sort(key=lambda s: (not s.get("favorite"), s.get("created_by") != "user", s.get("name", "")))
    return out


def get_style(style_id: str) -> dict | None:
    path = os.path.join(STYLE_DIR, f"{style_id}.json")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_style(recipe: dict, created_by: str = "user", style_id: str | None = None,
               favorite: bool | None = None, notes: str | None = None) -> dict:
    os.makedirs(STYLE_DIR, exist_ok=True)
    fixes: list[str] = []
    clean = validate_recipe(recipe, fixes)
    old = get_style(style_id) if style_id else None
    if not style_id:
        style_id = f"{_slug(clean['name'])}_{recipe_hash(clean)}"
        old = get_style(style_id)
    rec = {
        "id": style_id,
        "name": clean["name"],
        "recipe": clean,
        "created_by": (old or {}).get("created_by", created_by),
        "favorite": favorite if favorite is not None else (old or {}).get("favorite", created_by == "user"),
        "notes": notes if notes is not None else (old or {}).get("notes", ""),
        "created_at": (old or {}).get("created_at") or datetime.now().isoformat(timespec="seconds"),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
    with open(os.path.join(STYLE_DIR, f"{style_id}.json"), "w", encoding="utf-8") as f:
        json.dump(rec, f, ensure_ascii=False, indent=2)
    rec["fixes"] = fixes
    return rec


def delete_style(style_id: str) -> bool:
    """Pindahkan ke .trash (bisa dikembalikan manual), bukan dihapus permanen."""
    path = os.path.join(STYLE_DIR, f"{style_id}.json")
    if not os.path.exists(path):
        return False
    os.makedirs(TRASH_DIR, exist_ok=True)
    shutil.move(path, os.path.join(TRASH_DIR, f"{style_id}.json"))
    return True


# ------------------------------------------------------------------ builder ASS

def _ass_color(hex_color: str, opacity: float = 1.0) -> str:
    h = hex_color.lstrip("#")
    alpha = int(round((1 - opacity) * 255))
    return f"&H{alpha:02X}{h[4:6]}{h[2:4]}{h[0:2]}".upper()


def _ass_rgb(hex_color: str) -> str:
    h = hex_color.lstrip("#")
    return f"&H{h[4:6]}{h[2:4]}{h[0:2]}&".upper()


def _t(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def _esc(text: str) -> str:
    return text.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")


def line_metrics(recipe: dict) -> tuple[int, int, float]:
    """(tinggi kotak aman, tinggi baris, lebar per karakter) untuk penempatan."""
    size = recipe["size"]
    scale = max(recipe["animation"]["overshoot"] / 100, recipe["highlight"]["scale"],
                recipe["active_word"]["scale"] if recipe["active_word"]["mode"] == "scale" else 1.0)
    pad = recipe["box"]["padding"] if recipe["box"]["enabled"] else recipe["outline"]["width"]
    box_h = int(size * (1.25 * scale + 2 * pad))
    char_w = size * 0.55 * scale + recipe["letter_spacing"]
    return box_h, int(size * 1.25), char_w


def max_chars(recipe: dict) -> int:
    _, _, char_w = line_metrics(recipe)
    return max(4, int(980 / max(char_w, 1)))


def build_header(recipe: dict, ass_font: str) -> str:
    size = recipe["size"]
    fill = recipe["fill_colors"][0]
    if recipe["box"]["enabled"]:
        border_style, outline = 3, recipe["box"]["padding"] * size
        out_col = _ass_color(recipe["box"]["color"], recipe["box"]["opacity"])
    else:
        border_style, outline = 1, recipe["outline"]["width"] * size
        out_col = _ass_color(recipe["outline"]["color"])
    sh = recipe["shadow"]
    return (
        "[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\nWrapStyle: 2\n"
        "ScaledBorderAndShadow: yes\n\n[V4+ Styles]\n"
        "Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,"
        "Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,"
        "Alignment,MarginL,MarginR,MarginV,Encoding\n"
        f"Style: Sub,{ass_font},{size},{_ass_color(fill)},{_ass_color(fill)},{out_col},"
        f"{_ass_color(sh['color'], sh['opacity'])},0,0,0,0,100,100,{recipe['letter_spacing']},0,"
        f"{border_style},{outline:.1f},{sh['distance'] * size:.1f},2,30,30,0,1\n\n"
        "[Events]\nFormat: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text\n"
    )


def _line_tags(anim: dict, bottom: int) -> str:
    """Posisi + efek tingkat baris (geser, fade, wipe). \\move menggantikan \\pos."""
    d, a = anim["duration_ms"], anim["type"]
    if a == "slide_up":
        return f"\\an2\\move(540,{bottom + 45},540,{bottom},0,{d})\\fad({d},0)"
    tags = f"\\an2\\pos(540,{bottom})"
    if a == "fade":
        tags += f"\\fad({d},0)"
    elif a == "wipe":
        tags += f"\\clip(0,0,0,1920)\\t(0,{d},\\clip(0,0,1080,1920))"
    return tags


def _scale_tags(anim: dict, final_scale: float, animate: bool) -> str:
    """Ukuran per potong teks; pop/pantul dianimasikan di sini supaya tidak tertimpa tag kata."""
    d, f = anim["duration_ms"], int(final_scale * 100)
    if animate and anim["type"] == "pop":
        d1, o = int(d * 0.6), int(anim["overshoot"] * final_scale)
        return (f"\\fscx{anim['from_scale']}\\fscy{anim['from_scale']}\\t(0,{d1},\\fscx{o}\\fscy{o})"
                f"\\t({d1},{d},\\fscx{f}\\fscy{f})")
    if animate and anim["type"] == "bounce":
        o = int(anim["overshoot"] * final_scale)
        u = int((200 - anim["overshoot"]) * final_scale * 0.97)
        return (f"\\fscx{anim['from_scale']}\\fscy{anim['from_scale']}\\t(0,{int(d * .45)},\\fscx{o}\\fscy{o})"
                f"\\t({int(d * .45)},{int(d * .75)},\\fscx{u}\\fscy{u})\\t({int(d * .75)},{d},\\fscx{f}\\fscy{f})")
    return f"\\fscx{f}\\fscy{f}"


def build_events(chunks: list[dict], ys: list[int], recipe: dict, highlight_idx: set[int]) -> str:
    """Event ASS per chunk. chunks: {words:[(w,s,e)], idx:[i], start_seconds, end_seconds}."""
    size = recipe["size"]
    case = recipe["case"]
    cols = recipe["fill_colors"]
    aw, hl, anim = recipe["active_word"], recipe["highlight"], recipe["animation"]
    _, line_h, _ = line_metrics(recipe)
    word_no = 0
    out = []

    def fmt(w: str) -> str:
        return w.upper() if case == "upper" else w.lower() if case == "lower" else w

    for ci, (c, y) in enumerate(zip(chunks, ys)):
        bottom = y + line_h // 2
        words = c["words"]
        colors = []
        for k in range(len(words)):
            if recipe["color_mode"] == "cycle_word":
                colors.append(cols[(word_no + k) % len(cols)])
            elif recipe["color_mode"] == "cycle_line":
                colors.append(cols[ci % len(cols)])
            else:
                colors.append(cols[0])
        word_no += len(words)
        is_hl = [i in highlight_idx for i in c["idx"]]
        ends = [words[k + 1][1] for k in range(len(words) - 1)] + [c["end_seconds"]]

        if len(words) == 1:
            scale = hl["scale"] if is_hl[0] else 1.0
            col = hl["color"] if is_hl[0] else colors[0]
            tags = _line_tags(anim, bottom) + f"\\1c{_ass_rgb(col)}" + _scale_tags(anim, scale, True)
            out.append(f"Dialogue: 0,{_t(c['start_seconds'])},{_t(c['end_seconds'])},Sub,,0,0,0,,"
                       f"{{{tags}}}{_esc(fmt(words[0][0]))}")
            continue

        # Banyak kata: satu event per kata aktif; seluruh baris tampil, kata aktif diberi gaya
        for k in range(len(words)):
            start = c["start_seconds"] if k == 0 else words[k][1]
            parts = []
            for j, (w, _, _) in enumerate(words):
                col = hl["color"] if is_hl[j] else colors[j]
                tag = f"\\1c{_ass_rgb(col)}"
                sc = hl["scale"] if is_hl[j] else 1.0
                if j == k and aw["mode"] == "color":
                    tag = f"\\1c{_ass_rgb(aw['color'])}"
                elif j == k and aw["mode"] == "scale":
                    sc = max(sc, aw["scale"])
                elif j == k and aw["mode"] == "chip":
                    tag += f"\\3c{_ass_rgb(aw['color'])}\\bord{size * 0.22:.0f}"
                tag += _scale_tags(anim, sc, k == 0)
                reset = f"\\3c{_ass_rgb(recipe['outline']['color'])}\\bord{recipe['outline']['width'] * size:.1f}" \
                    if (j == k and aw["mode"] == "chip" and not recipe["box"]["enabled"]) else ""
                parts.append("{" + tag + "}" + _esc(fmt(w)) + ("{" + reset + "}" if reset else ""))
            lead = _line_tags(anim, bottom) if k == 0 else f"\\an2\\pos(540,{bottom})"
            out.append(f"Dialogue: 0,{_t(start)},{_t(ends[k])},Sub,,0,0,0,,{{{lead}}}" + " ".join(parts))
    return "\n".join(out) + "\n"
