"""Lapisan visual (Fase 8D): transisi di cut, stiker emoji, label nama, progress bar.

Semua di waktu video HASIL. Tidak ada panggilan Gemini di sini — emoji dipilih Gemini di
rencana; transisi, label, dan progress bar otomatis dari data yang sudah ada.
"""

from __future__ import annotations

import os

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
EMOJI_FONT = "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf"
EMOJI_CACHE = os.path.join(_ROOT, "tmp", "emoji")

# Emoji yang boleh dipilih Gemini (Noto Color Emoji, OFL). Disusun per rasa.
EMOJI_SET = ["😂", "🤣", "💀", "😭", "🥲", "😱", "😳", "🤯", "🔥", "💯", "👀", "🙏",
             "❤️", "😡", "🤔", "👏", "😅", "🫠", "😤", "✨", "💸", "🤝"]
LAUGH_EMOJI = {"😂", "🤣", "💀", "😅"}

EMOJI_SIZE = 190
EMOJI_DUR = 1.3
_FLASH_PEAK = 0.32
_FLASH_LEN = 0.12
_SETTLE_LEN = 0.35


# ------------------------------------------------------------------ 8D-1 transisi

def transition_plan(transitions: list[dict], style: str, scale: float) -> tuple[list[tuple], list[float], list[dict]]:
    """Dari laporan cut-ganti-layout (8A-2) → jendela zoom 'settle' + titik flash.

    Transisi visual dipasang di semua cut ganti layout KECUALI yang jatuh di beat tenang/penting
    (sama dengan alasan whoosh dilewati); SFX lain di dekatnya tidak menghalangi visual.
    """
    if style == "none":
        return [], [], []
    zooms, flashes, report = [], [], []
    k = 0
    for tr in transitions:
        if (tr.get("reason") or "").startswith("di beat"):
            continue
        kind = style if style in ("zoom", "flash") else ("zoom" if k % 2 == 0 else "flash")
        k += 1
        c = tr["t"]
        if kind == "zoom":
            zooms.append((c, c + _SETTLE_LEN, scale, "settle", 540, 960))
        else:
            flashes.append(c)
        report.append({"t": c, "kind": kind, "from": tr["from"], "to": tr["to"]})
    return zooms, flashes, report


def flash_filter(flashes: list[float]) -> str | None:
    if not flashes:
        return None
    terms = "+".join(
        f"if(between(t,{c - 0.04:.3f},{c + _FLASH_LEN:.3f}),{_FLASH_PEAK}*(1-abs(t-{c:.3f})/{_FLASH_LEN}),0)"
        for c in flashes)
    return f"eq=brightness='{terms}':eval=frame"


# ------------------------------------------------------------------ 8D-2 emoji

def emoji_png(emoji: str) -> str:
    """Render emoji dari Noto Color Emoji ke PNG transparan (cache)."""
    from PIL import Image, ImageDraw, ImageFont
    os.makedirs(EMOJI_CACHE, exist_ok=True)
    name = "-".join(f"{ord(ch):x}" for ch in emoji) + ".png"
    path = os.path.join(EMOJI_CACHE, name)
    if not os.path.exists(path):
        font = ImageFont.truetype(EMOJI_FONT, 109)          # font bitmap: hanya ukuran 109
        img = Image.new("RGBA", (160, 160), (0, 0, 0, 0))
        ImageDraw.Draw(img).text((80, 80), emoji, font=font, embedded_color=True, anchor="mm")
        bbox = img.getbbox()
        if bbox:
            img = img.crop(bbox)
        img.resize((EMOJI_SIZE * 2, int(EMOJI_SIZE * 2 * img.height / img.width)),
                   Image.LANCZOS).save(path)
    return path


def emoji_items(plan: dict, tl, zones, sub_bands: list[tuple]) -> list[dict]:
    """Posisi & waktu tiap emoji: dekat sisi layar, menghindari wajah dan subtitle."""
    from backend.editing.placement import choose_y
    out = []
    for k, e in enumerate(plan.get("emojis") or []):
        a = tl.map_time(e["s"])
        b = min(a + EMOJI_DUR, tl.duration)
        if b - a < 0.5:
            continue
        cx = 830 if k % 2 == 0 else 250
        avoid = [bd for (bs, be, bd) in sub_bands if bs < b and be > a]
        cy = choose_y(zones, a, b, EMOJI_SIZE + 20, EMOJI_SIZE + 20, 700, avoid=avoid, cx=cx)
        out.append({"emoji": e["emoji"], "t": round(a, 3), "end": round(b, 3), "cx": cx, "cy": cy,
                    "png": emoji_png(e["emoji"])})
    return out


def emoji_overlay_graph(items: list[dict], first_input: int, in_label: str, fps: float) -> tuple[list[str], str]:
    """Filter overlay emoji (pop-in + fade-out). Return (bagian graf, label keluaran)."""
    parts, cur = [], in_label
    for k, it in enumerate(items):
        a, b = it["t"], it["end"]
        u = f"(t-{a:.3f})"
        pop = (f"if(lt({u},0),0.3,if(lt({u},0.12),0.3+0.88*{u}/0.12,"
               f"if(lt({u},0.24),1.18-0.18*({u}-0.12)/0.12,1)))")
        parts.append(f"[{first_input + k}:v]format=rgba,"
                     f"scale=w='max(2,{EMOJI_SIZE}*{pop})':h=-1:eval=frame,"
                     f"fade=t=out:st={max(a, b - 0.2):.3f}:d=0.2:alpha=1[em{k}]")
        nxt = f"[vem{k}]"
        parts.append(f"{cur}[em{k}]overlay=x='{it['cx']}-overlay_w/2':y='{it['cy']}-overlay_h/2'"
                     f":eval=frame:enable='between(t,{a:.3f},{b:.3f})'{nxt}")
        cur = nxt
    return parts, cur


# ------------------------------------------------------------------ 8D-3 label nama

ROLE_LABEL = {"host": "Host", "guest": "Tamu", "co_host": "Co-host"}
_GENERIC_NAME = ("host", "tamu", "penanya", "audience", "penonton", "pembicara", "pria", "wanita",
                 "guest", "unknown", "narasumber", "orang", "id1", "id2", "id3", "id4", "id5")
LABEL_FS = 58
ROLE_FS = 30


def name_labels(ctx: dict, tl, plan: dict) -> list[dict]:
    """Label nama saat pembicara bernama (keyakinan ≥0.5) pertama kali tampil cukup lama."""
    from backend.analyzer.grid_composer import _cell_rects, output_face_zones_by_pid
    from backend.editing.context import shots_to_segments

    named = {p["id"]: p for p in plan.get("people") or [] if p.get("confidence", 0) >= 0.5
             and not any(g in p["name"].lower() for g in _GENERIC_NAME)}
    speakers = {p["id"] for p in ctx.get("people") or [] if p.get("speak_s", 0) > 0}
    segs = shots_to_segments(ctx["shots"])
    out, done = [], set()
    for piece in tl.pieces:
        if piece.teaser or piece.out_e - piece.out_s < 1.5:
            continue
        shot = ctx["shots"][piece.shot]
        faces = output_face_zones_by_pid(segs[piece.shot], ctx["width"], ctx["height"])
        for pid, iid in (shot.get("identity") or {}).items():
            if iid in done or iid not in named or iid not in speakers or pid not in faces:
                continue
            seg = segs[piece.shot]
            cx0, cy0, cw, ch = _cell_rects(seg.layout, len(seg.persons))[seg.persons.index(pid)]
            fx0, fy0, fx1, fy1 = faces[pid]
            # Lower third di dalam sel orang itu; naik bila mengenai wajah (kepala diperluas)
            head = (fx0 - (fx1 - fx0) * 0.25, fy0 - (fy1 - fy0) * 0.45, fx1 + (fx1 - fx0) * 0.25, fy1 + (fy1 - fy0) * 0.6)
            y = None
            for frac in (0.80, 0.70, 0.90, 0.60):
                cand = cy0 + ch * frac
                if not (head[1] - 60 < cand < head[3] + 60):
                    y = int(cand)
                    break
            if y is None:
                continue
            done.add(iid)
            start = piece.out_s + 0.3
            out.append({"id": iid, "name": named[iid]["name"], "role": ROLE_LABEL.get(named[iid].get("role"), ""),
                        "t": round(start, 3), "end": round(min(start + 2.5, piece.out_e), 3),
                        "cx": cx0 + cw // 2, "y": y, "cell": (cx0, cy0, cw, ch)})
        if len(done) >= 3:
            break
    return out


def label_events(labels: list[dict], accent: str) -> tuple[list[str], list[tuple]]:
    """Event ASS (kotak warna aksen, geser masuk) + area yang harus dihindari subtitle."""
    from backend.editing.subtitle_style import _ass_rgb, _esc, _t
    events, bands = [], []
    for lb in labels:
        w = int(len(lb["name"]) * LABEL_FS * 0.62) + 60
        cx = max(w // 2 + 20, min(1080 - w // 2 - 20, lb["cx"]))
        y = max(120, min(1800, lb["y"]))
        name = (f"{{\\an5\\move({cx - 60},{y},{cx},{y},0,220)\\fad(180,220)\\fnMontserrat Black\\fs{LABEL_FS}"
                f"\\1c&HFFFFFF&\\3c{_ass_rgb(accent)}\\bord14\\shad0\\blur0.6}}{_esc(lb['name'])}")
        events.append(f"Dialogue: 3,{_t(lb['t'])},{_t(lb['end'])},Overlay,,0,0,0,,{name}")
        if lb["role"]:
            role = (f"{{\\an5\\move({cx - 60},{y + 62},{cx},{y + 62},60,280)\\fad(240,220)\\fnMontserrat Black"
                    f"\\fs{ROLE_FS}\\1c&HFFFFFF&\\3c&H000000&\\bord3\\shad0}}{_esc(lb['role'].upper())}")
            events.append(f"Dialogue: 3,{_t(lb['t'])},{_t(lb['end'])},Overlay,,0,0,0,,{role}")
        bands.append((lb["t"], lb["end"], (cx - w // 2, y - 50, cx + w // 2, y + 95)))
    return events, bands


# ------------------------------------------------------------------ 8D-4 progress bar

def progress_graph(in_label: str, total: float, color: str, fps: float, height: int = 8) -> tuple[list[str], str]:
    c = color.lstrip("#")
    return ([f"color=c=0x{c}@0.9:s=1080x{height}:r={fps:g}:d={total:.3f}[pbar]",
             f"{in_label}[pbar]overlay=x='-w+w*t/{total:.3f}':y=0:eval=frame:eof_action=pass[vpb]"], "[vpb]")
