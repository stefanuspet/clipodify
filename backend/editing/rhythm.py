"""Peta ritme (Fase 8A-1): kapan tampilan/suara berubah, dan celah tanpa perubahan.

"Perubahan" = cut kamera, teaser, zoom, SFX, teks overlay, efek, end card.
Subtitle tidak dihitung (selalu ada); jump cut dari potong jeda juga tidak (framing sama,
nyaris tak terlihat). Tahap ini hanya MENGUKUR — tidak mengubah render.
"""

from __future__ import annotations

MERGE_S = 0.3        # perubahan yang berdekatan dihitung satu
REPORT_GAP_S = 4.0   # celah sepanjang ini dilaporkan
LONG_GAP_S = 6.0     # celah sepanjang ini ditandai "terlalu lama" (dipakai aturan 8A-4)


def effective_t(s: dict) -> float:
    """Saat SFX benar-benar 'terdengar': titik selaras (build-up), puncak (otomatis), atau awal."""
    if "align_t" in s:
        return s["align_t"]
    return s["t"] + s.get("peak_s", 0.0) if s.get("auto") else s["t"]


def _gaps(times: list[float], total: float) -> list[dict]:
    pts = [0.0] + sorted(t for t in times if 0 < t < total) + [total]
    out = []
    for a, b in zip(pts, pts[1:]):
        if b - a >= REPORT_GAP_S:
            out.append({"s": round(a, 2), "e": round(b, 2), "len": round(b - a, 2), "long": b - a >= LONG_GAP_S})
    return out


def _summary(events: list[dict], total: float) -> dict:
    merged: list[dict] = []
    for ev in sorted(events, key=lambda e: e["t"]):
        if merged and ev["t"] - merged[-1]["t"] < MERGE_S:
            merged[-1]["types"] = sorted(set(merged[-1]["types"]) | {ev["type"]})
            continue
        merged.append({"t": round(ev["t"], 2), "types": [ev["type"]]})
    gaps = _gaps([e["t"] for e in merged], total)
    n = len(merged)
    return {
        "events": merged,
        "count": n,
        "per_10s": round(n / total * 10, 2) if total else 0.0,
        "avg_interval": round(total / (n + 1), 2) if total else 0.0,
        "max_gap": max((g["len"] for g in gaps), default=0.0),
        "gaps": gaps,
        "long_gaps": sum(1 for g in gaps if g["long"]),
    }


def measure(tl, ctx: dict, zooms: list[dict], sfx: list[dict], texts: list[dict],
            effects: list[dict]) -> dict:
    """Ritme video HASIL (waktu output). zooms dalam waktu klip asli; sisanya sudah waktu output."""
    events = []
    for a, b in zip(tl.pieces, tl.pieces[1:]):
        if a.teaser != b.teaser:
            events.append({"t": b.out_s, "type": "teaser"})
        elif a.shot != b.shot:
            events.append({"t": b.out_s, "type": "cut"})
    events += [{"t": tl.map_time(z["s"]), "type": "zoom"} for z in zooms]
    events += [{"t": effective_t(s), "type": s.get("kind", "sfx")} for s in sfx]
    events += [{"t": t["start"], "type": "end_card" if t.get("role") == "end_card" else "teks"} for t in texts]
    events += [{"t": e["start"], "type": "efek"} for e in effects]
    return _summary(events, tl.total)


def base_rhythm(ctx: dict) -> dict:
    """Ritme bawaan klip asli (hanya cut kamera) — diberikan ke Gemini sebelum ia merencanakan."""
    cuts = [{"t": sh["t_start"], "type": "cut"} for sh in ctx["shots"][1:]]
    return _summary(cuts, float(ctx["duration"]))


def describe(r: dict, label: str) -> str:
    gaps = ", ".join(f"{g['s']:.1f}–{g['e']:.1f}s ({g['len']:.1f} dtk)" for g in r["gaps"]) or "tidak ada"
    return (f"{label}: {r['count']} perubahan, rata-rata tiap {r['avg_interval']:.1f} dtk, "
            f"celah terpanjang {r['max_gap']:.1f} dtk. Celah ≥{REPORT_GAP_S:.0f} dtk: {gaps}")
