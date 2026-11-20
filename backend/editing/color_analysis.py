"""Analisis warna tampilan AKHIR (kanvas 1080×1920 setelah grid/crop) per shot.

Dipakai untuk:
1. Data ke Gemini — warna area subtitle & statistik grade, supaya tidak menebak buta.
2. Simulasi grade — resep grade diterapkan ke frame sampel lalu diukur; bila merusak
   (highlight terpotong, kulit terlalu oranye, saturasi berlebihan) nilainya dikurangi.
3. Cek kontras warna subtitle terhadap warna area tempat subtitle jatuh.
Tanpa panggilan Gemini. Perhitungan grade adalah aproksimasi rumus eq/colorbalance FFmpeg.
"""

from __future__ import annotations

import logging

import cv2
import numpy as np

logger = logging.getLogger(__name__)

CANVAS_W, CANVAS_H = 540, 960          # setengah resolusi output, cukup untuk statistik warna
_SAMPLES = (0.25, 0.5, 0.75)
# Area kandidat posisi subtitle (koordinat kanvas 1080×1920), sama dengan placement.PREFERRED_Y
AREAS = {"top": (170, 370), "center": (860, 1060), "bottom": (1460, 1680)}


# ------------------------------------------------------------------ kanvas

def compose_canvas(frame: np.ndarray, seg, vid_w: int, vid_h: int,
                   W: int = CANVAS_W, H: int = CANVAS_H) -> np.ndarray:
    """Susun ulang frame jadi tampilan akhir (grid/crop identik dengan renderer)."""
    from backend.analyzer.grid_composer import _cell_rects, _crop_rect
    s = W / 1080
    canvas = np.zeros((H, W, 3), np.uint8)
    n = len(seg.persons)
    cells = _cell_rects(seg.layout, n)
    xb = seg.x_bounds or {}
    for j, pid in enumerate(seg.persons):
        if j >= len(cells):
            break
        cx, cy, cw, ch = cells[j]
        bb = seg.bboxes.get(pid)
        if bb is None:
            w = min(vid_w, int(vid_h * cw / ch)); h = min(vid_h, int(vid_w * ch / cw))
            rx, ry = (vid_w - w) // 2, (vid_h - h) // 2
        else:
            fb = (seg.face_bboxes or {}).get(pid)
            lo, hi = xb.get(pid, (0, vid_w)) if n > 1 else (0, vid_w)
            w, h, rx, ry, _ = _crop_rect(bb, vid_w, vid_h, cw, ch, fb, lo, hi)
        crop = frame[ry:ry + h, rx:rx + w]
        if crop.size == 0:
            continue
        tw, th = max(1, round(cw * s)), max(1, round(ch * s))
        sc = max(tw / crop.shape[1], th / crop.shape[0])
        rw, rh = max(tw, int(crop.shape[1] * sc + 0.5)), max(th, int(crop.shape[0] * sc + 0.5))
        r = cv2.resize(crop, (rw, rh), interpolation=cv2.INTER_AREA)
        ox = (rw - tw) // 2
        x0, y0 = round(cx * s), round(cy * s)
        tile = r[0:th, ox:ox + tw]
        canvas[y0:y0 + tile.shape[0], x0:x0 + tile.shape[1]] = tile[:H - y0, :W - x0]
    return canvas


# ------------------------------------------------------------------ warna

def color_name(rgb) -> str:
    r, g, b = [int(x) for x in rgb]
    h, s, v = cv2.cvtColor(np.uint8([[[b, g, r]]]), cv2.COLOR_BGR2HSV)[0, 0]
    h, s, v = int(h) * 2, s / 255, v / 255
    if v < 0.18:
        return "hitam"
    if s < 0.15:
        return "putih" if v > 0.85 else "abu terang" if v > 0.55 else "abu gelap"
    if (h < 40 or h > 340) and s < 0.5 and v > 0.55:
        return "krem/kulit" if h < 40 else "pink pucat"
    names = [(15, "merah"), (40, "oranye"), (65, "kuning"), (160, "hijau"), (195, "tosca"),
             (250, "biru"), (290, "ungu"), (335, "pink"), (361, "merah")]
    base = next(n for lim, n in names if h < lim)
    if base in ("oranye", "merah") and v < 0.6:
        base = "coklat"
    return base + (" gelap" if v < 0.45 else "")


def _hex(rgb) -> str:
    return "#%02X%02X%02X" % tuple(int(x) for x in rgb)


def dominant_colors(img_rgb: np.ndarray, k: int = 3) -> list[dict]:
    px = img_rgb.reshape(-1, 3)
    if len(px) > 3000:
        px = px[np.random.default_rng(0).choice(len(px), 3000, replace=False)]
    lab = cv2.cvtColor(px.reshape(-1, 1, 3).astype(np.uint8), cv2.COLOR_RGB2LAB).reshape(-1, 3).astype(np.float32)
    k = min(k, len(lab))
    _, labels, centers = cv2.kmeans(lab, k, None, (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 20, 1.0),
                                    3, cv2.KMEANS_PP_CENTERS)
    out = []
    for c in range(k):
        share = float(np.mean(labels.ravel() == c))
        rgb = cv2.cvtColor(centers[c].reshape(1, 1, 3).astype(np.uint8), cv2.COLOR_LAB2RGB)[0, 0]
        out.append({"hex": _hex(rgb), "name": color_name(rgb), "share": round(share, 2)})
    return sorted(out, key=lambda x: -x["share"])


def image_stats(img_rgb: np.ndarray) -> dict:
    f = img_rgb.astype(np.float32) / 255
    y = 0.2126 * f[..., 0] + 0.7152 * f[..., 1] + 0.0722 * f[..., 2]
    hsv = cv2.cvtColor(img_rgb, cv2.COLOR_RGB2HSV)
    return {
        "luma": round(float(y.mean()), 3),
        "contrast": round(float(y.std()), 3),
        "saturation": round(float(hsv[..., 1].mean() / 255), 3),
        "clip_high": round(float(np.mean(y > 0.97)), 4),
        "clip_low": round(float(np.mean(y < 0.03)), 4),
        "warm_cast": round(float((f[..., 0] - f[..., 2]).mean()), 3),     # + hangat, − dingin
        "tint_cast": round(float((f[..., 1] - (f[..., 0] + f[..., 2]) / 2).mean()), 3),  # + hijau, − magenta
    }


def _skin_pixels(canvas_rgb: np.ndarray, seg, vid_w: int, vid_h: int) -> np.ndarray:
    from backend.analyzer.grid_composer import output_face_zones
    s = canvas_rgb.shape[1] / 1080
    parts = []
    for x0, y0, x1, y1 in output_face_zones(seg, vid_w, vid_h):
        w, h = x1 - x0, y1 - y0
        # bagian tengah wajah (pipi/dahi), hindari rambut, hijab, latar
        a, b = int((x0 + w * 0.3) * s), int((x1 - w * 0.3) * s)
        c, d = int((y0 + h * 0.35) * s), int((y1 - h * 0.25) * s)
        p = canvas_rgb[c:d, a:b]
        if p.size:
            parts.append(p.reshape(-1, 3))
    return np.concatenate(parts) if parts else np.zeros((0, 3), np.uint8)


def analyze_colors(ctx: dict) -> list[dict]:
    """Statistik warna per shot dari tampilan akhir. Disimpan di ctx['colors']."""
    from backend.analyzer.grid_composer import layout_seams
    from backend.editing.context import shots_to_segments

    segs = shots_to_segments(ctx["shots"])
    cap = cv2.VideoCapture(ctx["clip_path"])
    vid_w, vid_h, fps = ctx["width"], ctx["height"], ctx["fps"]
    out = []
    for k, (sh, seg) in enumerate(zip(ctx["shots"], segs)):
        canvases = []
        for q in _SAMPLES:
            cap.set(cv2.CAP_PROP_POS_FRAMES, int((sh["t_start"] + (sh["t_end"] - sh["t_start"]) * q) * fps))
            ok, frame = cap.read()
            if ok:
                canvases.append(cv2.cvtColor(compose_canvas(frame, seg, vid_w, vid_h), cv2.COLOR_BGR2RGB))
        if not canvases:
            continue
        stack = np.concatenate(canvases, axis=0)
        s = CANVAS_W / 1080
        areas = {}
        for name, (y0, y1) in AREAS.items():
            band = np.concatenate([c[int(y0 * s):int(y1 * s)] for c in canvases], axis=0)
            areas[name] = dominant_colors(band)
        for y in layout_seams(sh["layout"], len(sh["persons"])):
            band = np.concatenate([c[int((y - 100) * s):int((y + 100) * s)] for c in canvases], axis=0)
            areas[f"seam_{y}"] = dominant_colors(band)
        skin = np.concatenate([_skin_pixels(c, seg, vid_w, vid_h) for c in canvases])
        skin_info = None
        if len(skin) > 50:
            m = np.median(skin, axis=0)
            hsv = cv2.cvtColor(np.uint8([[m]]), cv2.COLOR_RGB2HSV)[0, 0]
            skin_info = {"hex": _hex(m), "saturation": round(float(hsv[1]) / 255, 3)}
        out.append({"shot": k, **image_stats(stack), "skin": skin_info, "areas": areas})
    cap.release()
    return out


# ------------------------------------------------------------------ simulasi grade

def apply_grade(img_rgb: np.ndarray, g: dict) -> np.ndarray:
    """Aproksimasi eq (kontras, kecerahan, gamma, saturasi) + colorbalance (hangat, tint) FFmpeg."""
    f = img_rgb.astype(np.float32) / 255
    y = 0.299 * f[..., 0] + 0.587 * f[..., 1] + 0.114 * f[..., 2]
    cb, cr = f[..., 2] - y, f[..., 0] - y
    y2 = np.clip((y - 0.5) * g["contrast"] + 0.5 + g["brightness"], 0, 1) ** (1 / max(g["gamma"], 0.05))
    cb, cr = cb * g["saturation"], cr * g["saturation"]
    r = y2 + cr
    b = y2 + cb
    gg = (y2 - 0.299 * r - 0.114 * b) / 0.587
    out = np.stack([r, gg, b], axis=-1)
    w, t = g.get("warmth", 0.0), g.get("tint", 0.0)
    if abs(w) > 1e-3 or abs(t) > 1e-3:
        ws = np.clip(1 - y2 / 0.4, 0, 1)
        wh = np.clip((y2 - 0.6) / 0.4, 0, 1)
        wm = np.clip(1 - ws - wh, 0, 1)
        wt = (ws + wm + 0.6 * wh)[..., None]
        delta = np.array([0.10 * w - 0.03 * t, 0.08 * t, -0.10 * w - 0.03 * t], np.float32)
        out = out + wt * delta * 0.5
    return (np.clip(out, 0, 1) * 255).astype(np.uint8)


NEUTRAL = {"contrast": 1.0, "saturation": 1.0, "brightness": 0.0, "gamma": 1.0, "warmth": 0.0, "tint": 0.0}


def _samples_for_grade(ctx: dict) -> list[tuple[np.ndarray, np.ndarray]]:
    """(kanvas, piksel kulit) satu frame tengah per shot, resolusi kecil."""
    from backend.editing.context import shots_to_segments
    segs = shots_to_segments(ctx["shots"])
    cap = cv2.VideoCapture(ctx["clip_path"])
    out = []
    for sh, seg in zip(ctx["shots"], segs):
        cap.set(cv2.CAP_PROP_POS_FRAMES, int((sh["t_start"] + sh["t_end"]) / 2 * ctx["fps"]))
        ok, frame = cap.read()
        if not ok:
            continue
        c = cv2.cvtColor(compose_canvas(frame, seg, ctx["width"], ctx["height"], 270, 480), cv2.COLOR_BGR2RGB)
        out.append((c, _skin_pixels(c, seg, ctx["width"], ctx["height"])))
    cap.release()
    return out


def _luma(img_rgb: np.ndarray) -> np.ndarray:
    f = img_rgb.astype(np.float32) / 255
    return 0.2126 * f[..., 0] + 0.7152 * f[..., 1] + 0.0722 * f[..., 2]


def _measure(samples, g) -> dict:
    """Ukur hasil grade relatif terhadap aslinya: detail yang HILANG, bukan area yang memang gelap/terang."""
    lost_hi, lost_lo, sat, skin_sat, skin_luma = [], [], [], [], []
    for c, skin in samples:
        y0 = _luma(c)
        out = apply_grade(c, g)
        y1 = _luma(out)
        mid_hi = (y0 > 0.80) & (y0 < 0.94)          # highlight yang masih punya detail
        mid_lo = (y0 > 0.06) & (y0 < 0.20)          # bayangan yang masih punya detail
        lost_hi.append(float(np.mean(mid_hi & (y1 > 0.985))))
        lost_lo.append(float(np.mean(mid_lo & (y1 < 0.02))))
        sat.append(float(cv2.cvtColor(out, cv2.COLOR_RGB2HSV)[..., 1].mean() / 255))
        if len(skin) > 20:
            sk = apply_grade(skin.reshape(-1, 1, 3), g).reshape(-1, 1, 3)
            skin_sat.append(float(np.median(cv2.cvtColor(sk, cv2.COLOR_RGB2HSV)[..., 1]) / 255))
            skin_luma.append(float(np.median(_luma(sk))))
    return {"lost_hi": float(np.mean(lost_hi)), "lost_lo": float(np.mean(lost_lo)),
            "saturation": float(np.mean(sat)), "skin_saturation": float(np.mean(skin_sat)) if skin_sat else None,
            "skin_luma": float(np.mean(skin_luma)) if skin_luma else None}


def check_grade(ctx: dict, grade: dict, max_iter: int = 6) -> tuple[dict, list[str]]:
    """Terapkan grade ke sampel, ukur hasilnya; kurangi intensitas sampai tidak merusak."""
    samples = _samples_for_grade(ctx)
    if not samples:
        return grade, []
    before = _measure(samples, NEUTRAL)
    g = dict(grade)
    reasons: list[str] = []
    for _ in range(max_iter):
        after = _measure(samples, g)
        problems = []
        if after["lost_hi"] > 0.01:
            problems.append(f"detail highlight hilang {after['lost_hi'] * 100:.1f}% layar")
        if after["lost_lo"] > 0.015:
            problems.append(f"detail bayangan hilang {after['lost_lo'] * 100:.1f}% layar")
        if after["saturation"] > 0.62 and after["saturation"] - before["saturation"] > 0.05:
            problems.append(f"saturasi berlebihan {before['saturation']:.2f}→{after['saturation']:.2f}")
        if (after["skin_saturation"] and before["skin_saturation"]
                and after["skin_saturation"] > 0.6 and after["skin_saturation"] - before["skin_saturation"] > 0.08):
            problems.append(f"kulit terlalu jenuh/oranye {before['skin_saturation']:.2f}→{after['skin_saturation']:.2f}")
        if (after["skin_luma"] and before["skin_luma"]
                and after["skin_luma"] > 0.72 and after["skin_luma"] - before["skin_luma"] > 0.08):
            problems.append(f"wajah terlalu terang/pucat {before['skin_luma']:.2f}→{after['skin_luma']:.2f}")
        if not problems:
            break
        reasons = problems
        g = {k: NEUTRAL[k] + (v - NEUTRAL[k]) * 0.7 for k, v in g.items()}
    else:
        g = dict(NEUTRAL)
    fixes = []
    if g != grade:
        fixes.append("grade dikurangi setelah disimulasikan (" + "; ".join(reasons) + ")")
        g = {k: round(v, 3) for k, v in g.items()}
    return g, fixes


# ------------------------------------------------------------------ kontras subtitle

def _lum(hex_color: str) -> float:
    h = hex_color.lstrip("#")
    def ch(v):
        v = int(v, 16) / 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = ch(h[0:2]), ch(h[2:4]), ch(h[4:6])
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(a: str, b: str) -> float:
    la, lb = sorted((_lum(a), _lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def check_subtitle_contrast(colors: list[dict], recipe: dict) -> tuple[dict, list[str]]:
    """Teks tenggelam bila isi DAN outline sama-sama mirip warna area. Perbaiki outline/kotak."""
    if not colors or recipe["box"]["enabled"]:
        return recipe, []
    area = recipe["position"]
    doms = [c for sh in colors for c in sh["areas"].get(area, [])[:2] if c["share"] >= 0.25]
    if not doms:
        return recipe, []
    out = recipe["outline"]["color"]
    bad = [(fc, d) for fc in recipe["fill_colors"] for d in doms
           if contrast_ratio(fc, d["hex"]) < 1.8 and (recipe["outline"]["width"] < 0.05
                                                      or contrast_ratio(out, d["hex"]) < 1.8)]
    if not bad:
        return recipe, []
    fc, d = bad[0]
    r = {**recipe, "outline": dict(recipe["outline"])}
    r["outline"]["color"] = "#000000" if _lum(d["hex"]) > 0.18 else "#FFFFFF"
    r["outline"]["width"] = max(r["outline"]["width"], 0.12)
    return r, [f"subtitle: warna {fc} dan outline tenggelam di area {area} ({d['name']} {d['hex']}) "
               f"→ outline {r['outline']['color']} {r['outline']['width']}"]
