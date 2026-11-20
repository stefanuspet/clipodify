"""Fase 6: belajar dari performa — pilihan edit mana yang menaikkan share & komentar.

Alur: setiap render Auto Edit dicatat (edit_renders) → user menghubungkannya ke video
YouTube yang diupload → laporan membandingkan fitur edit vs reaksi penonton →
plan dengan reaksi terbaik diusulkan jadi contoh persona (diterapkan hanya bila user setuju).
"""

from __future__ import annotations

import glob
import json
import os
import re
from difflib import SequenceMatcher

MIN_VIEWS = 100          # di bawah ini rate share/komentar terlalu acak untuk dibandingkan
MIN_GROUP = 3            # minimal video per kelompok sebelum menarik kesimpulan
MIN_RATIO = 1.5          # selisih minimal (kali lipat) supaya dianggap beda nyata


# ------------------------------------------------------------------ fitur

def _grade_label(g: dict) -> str | None:
    if "preset" in g:                       # plan versi lama (preset)
        return g["preset"]
    w = g.get("warmth")
    if w is None:
        return None
    return "hangat" if w > 0.15 else "dingin" if w < -0.15 else "netral"


def plan_features(rec: dict) -> dict:
    """Ringkas satu plan record jadi fitur yang bisa dibandingkan. Tahan terhadap plan versi lama."""
    p = rec.get("plan") or {}
    r = rec.get("render") or {}
    fixes = rec.get("fixes") or []
    texts = p.get("text_overlays") or []
    ec = p.get("end_card") or {}
    src = r.get("source_duration") or 0
    return {
        "intent": p.get("intent"),
        "subtitle_animation": ((p.get("subtitle") or {}).get("recipe") or {}).get("animation", {}).get("type")
        or (p.get("subtitle") or {}).get("animation"),
        "subtitle_style": (p.get("subtitle") or {}).get("style_id"),
        "subtitle_font": (((p.get("subtitle") or {}).get("recipe") or {}).get("font") or {}).get("family")
        or (p.get("subtitle") or {}).get("font"),
        "grade": _grade_label(p.get("grade") or {}),
        "sfx_count": len(p.get("sfx") or []),
        "sfx_tags": sorted({s.get("tag") or s.get("sfx_id", "") for s in p.get("sfx") or []}),
        "text_count": len(texts),
        "has_hook_title": any(t.get("role") == "hook_title" for t in texts),
        "has_question": any(t.get("role") == "question" for t in texts) or bool(ec.get("enabled")),
        "has_end_card": bool(ec.get("enabled")),
        "has_teaser": bool((p.get("hook_teaser") or {}).get("enabled")),
        "zoom_count": len(p.get("zooms") or []),
        "music": (r.get("music") or {}).get("title") and (p.get("music") or {}).get("mood") or "none",
        "cut_ratio": round((r.get("cut_seconds") or 0) / src, 3) if src else 0.0,
        "highlight_count": len((p.get("subtitle") or {}).get("highlight_words") or []),
        "beats_count": len(p.get("beats") or []),
        "generic_question": any("pertanyaan generik" in f for f in fixes),
        "max_gap": (r.get("rhythm") or {}).get("max_gap"),
        "changes_per_10s": (r.get("rhythm") or {}).get("per_10s"),
    }


def record_from_file(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        rec = json.load(f)
    persona = rec.get("persona") or {}
    return {
        "render_id": rec["render_id"], "candidate_id": rec["candidate_id"],
        "created_at": rec.get("created_at") or "", "output_path": rec.get("output_path"),
        "plan_file": path, "persona_id": persona.get("id"), "persona_version": persona.get("version"),
        "model": rec.get("model"), "intent": (rec.get("plan") or {}).get("intent"),
        "features": plan_features(rec),
    }


def backfill(cache_root: str) -> int:
    """Daftarkan plan_*.json yang belum ada di DB (render sebelum Fase 6)."""
    from db.database import _connect, known_edit_render_ids, save_edit_render
    known = known_edit_render_ids()
    added = 0
    for path in glob.glob(os.path.join(cache_root, "*", "plan_*.json")):
        try:
            r = record_from_file(path)
        except (KeyError, json.JSONDecodeError):
            continue
        if r["render_id"] in known:
            continue
        with _connect() as conn:
            row = conn.execute("SELECT title FROM candidates WHERE id = ?", (r["candidate_id"],)).fetchone()
        r["candidate_title"] = row["title"] if row else None
        save_edit_render(r)
        added += 1
    return added


# ------------------------------------------------------------------ pasangan video

def suggest_videos(render: dict, videos: list[dict], top: int = 3) -> list[dict]:
    """Video YouTube yang kemungkinan hasil upload render ini: tayang sesudahnya, judul mirip."""
    made = (render.get("created_at") or "")[:10]
    title = (render.get("candidate_title") or "").lower()
    out = []
    for v in videos:
        if made and (v.get("published_at") or "")[:10] < made:
            continue
        score = SequenceMatcher(None, title, (v.get("title") or "").lower()).ratio() if title else 0.0
        out.append({"video_id": v["video_id"], "title": v.get("title"), "published_at": v.get("published_at"),
                    "similarity": round(score, 2)})
    out.sort(key=lambda x: -x["similarity"])
    return out[:top]


# ------------------------------------------------------------------ laporan

def _metrics(v: dict) -> dict | None:
    views = v.get("views") or 0
    if views < MIN_VIEWS:
        return None
    return {
        "views": views,
        "share_rate": 100.0 * (v.get("shares") or 0) / views,
        "comment_rate": 100.0 * (v.get("comments") or 0) / views,
        "like_rate": 100.0 * (v.get("likes") or 0) / views,
        "retention": v.get("avg_view_percentage"),
        "has_engagement": v.get("shares") is not None and v.get("comments") is not None,
    }


def _bucket(feature: str, value):
    if feature == "sfx_count":
        return "0" if value == 0 else "1–2" if value <= 2 else "3+"
    if feature == "zoom_count":
        return "0" if value == 0 else "1+"
    if feature == "cut_ratio":
        return "<3%" if value < 0.03 else "3–8%" if value <= 0.08 else ">8%"
    if feature == "max_gap":
        return "<4 dtk" if value < 4 else "4–8 dtk" if value <= 8 else ">8 dtk"
    if feature == "changes_per_10s":
        return "<2" if value < 2 else "2–4" if value <= 4 else ">4"
    if feature == "highlight_count":
        return "0–5" if value <= 5 else "6–12" if value <= 12 else "13+"
    return str(value)


COMPARE = ["intent", "subtitle_style", "subtitle_animation", "grade", "has_question", "has_end_card", "has_hook_title",
           "has_teaser", "generic_question", "max_gap", "changes_per_10s", "music", "sfx_count", "zoom_count", "cut_ratio",
           "highlight_count"]

LABELS = {
    "intent": "Jenis klip", "subtitle_style": "Gaya subtitle", "subtitle_animation": "Animasi subtitle", "grade": "Grade warna",
    "has_question": "Ada pertanyaan penutup", "has_end_card": "Pakai end card",
    "has_hook_title": "Pakai judul hook", "has_teaser": "Pakai teaser hook",
    "generic_question": "Pertanyaan generik", "music": "Musik latar", "sfx_count": "Jumlah SFX",
    "zoom_count": "Jumlah zoom", "max_gap": "Celah terpanjang", "changes_per_10s": "Perubahan per 10 dtk", "cut_ratio": "Porsi dipotong", "highlight_count": "Kata highlight",
}


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return round(sum(xs) / len(xs), 3) if xs else None


def build_report(renders: list[dict], videos: list[dict]) -> dict:
    by_id = {v["video_id"]: v for v in videos}
    rows = []
    for r in renders:
        v = by_id.get(r.get("yt_video_id") or "")
        m = _metrics(v) if v else None
        if m:
            rows.append({**r, "metrics": m, "video_title": v.get("title")})

    status = {
        "renders": len(renders),
        "linked": sum(1 for r in renders if r.get("yt_video_id")),
        "usable": len(rows),
        "missing_engagement": sum(1 for x in rows if not x["metrics"]["has_engagement"]),
    }

    comparisons, insights = [], []
    for feat in COMPARE:
        groups: dict[str, list] = {}
        for x in rows:
            value = x["features"].get(feat)
            if value is None:          # plan versi lama belum punya fitur ini
                continue
            groups.setdefault(_bucket(feat, value), []).append(x["metrics"])
        if len(groups) < 2:
            continue
        stats = [{"value": k, "n": len(ms),
                  "share_rate": _mean([m["share_rate"] for m in ms]),
                  "comment_rate": _mean([m["comment_rate"] for m in ms]),
                  "retention": _mean([m["retention"] for m in ms]),
                  "views": _mean([m["views"] for m in ms])} for k, ms in groups.items()]
        stats.sort(key=lambda g: -g["n"])
        enough = all(g["n"] >= MIN_GROUP for g in stats)
        comparisons.append({"feature": feat, "label": LABELS.get(feat, feat), "groups": stats,
                            "enough_data": enough})
        if not enough:
            continue
        for metric, name in (("share_rate", "share"), ("comment_rate", "komentar")):
            vals = [g for g in stats if g[metric]]
            if len(vals) < 2:
                continue
            hi, lo = max(vals, key=lambda g: g[metric]), min(vals, key=lambda g: g[metric])
            if lo[metric] > 0 and hi[metric] / lo[metric] >= MIN_RATIO:
                insights.append(
                    f"{LABELS.get(feat, feat)} = {hi['value']}: {name} {hi[metric] / lo[metric]:.1f}× lebih "
                    f"tinggi dibanding {lo['value']} (n={hi['n']} vs {lo['n']})")

    ranked = sorted(rows, key=lambda x: -(x["metrics"]["share_rate"] + x["metrics"]["comment_rate"]))
    best = [{"render_id": x["render_id"], "plan_file": x["plan_file"], "video_title": x["video_title"],
             "share_rate": round(x["metrics"]["share_rate"], 3),
             "comment_rate": round(x["metrics"]["comment_rate"], 3), "views": x["metrics"]["views"],
             "intent": x["features"].get("intent")} for x in ranked[:3] if x["metrics"]["has_engagement"]]
    return {"status": status, "comparisons": comparisons, "insights": insights, "best_plans": best,
            "thresholds": {"min_views": MIN_VIEWS, "min_group": MIN_GROUP, "min_ratio": MIN_RATIO}}


# ------------------------------------------------------------------ persona

def apply_examples(persona_path: str, plan_files: list[str], root: str) -> list[str]:
    """Tulis daftar contoh ke `examples:` persona tanpa merusak komentar lain di file YAML."""
    rel = [os.path.relpath(p, root) if os.path.isabs(p) else p for p in plan_files]
    with open(persona_path, encoding="utf-8") as f:
        text = f.read()
    block = "examples:\n" + "".join(f"  - {p}\n" for p in rel) if rel else "examples: []\n"
    new, n = re.subn(r"^examples:.*?(?=^\S|\Z)", block, text, flags=re.M | re.S)
    if n == 0:
        new = text.rstrip("\n") + "\n\n" + block
    with open(persona_path, "w", encoding="utf-8") as f:
        f.write(new)
    return rel
