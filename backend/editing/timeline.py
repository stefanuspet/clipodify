"""Timeline hasil edit: bagian klip mana yang dipakai, dalam urutan apa.

Semua tool yang mengubah urutan/durasi (potong jeda, potong kata pengisi, potongan
pilihan Gemini, teaser hook di depan) bekerja di sini. Tool lain cukup memetakan
waktu klip asli → waktu video hasil lewat `Timeline.map_time`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

FILLERS = {"eh", "ehm", "em", "emm", "hmm", "hm", "eee", "ee", "e", "uh", "um", "eung"}
SILENCE_MIN_GAP_S = 0.45   # jeda antar kata lebih panjang dari ini dipotong
EDGE_PAD_S = 0.12          # sisa napas di kiri-kanan potongan supaya tidak terdengar terpenggal
MIN_PIECE_FRAMES = 4


@dataclass
class Piece:
    src_s: float
    src_e: float
    out_s: float
    out_e: float
    shot: int
    teaser: bool = False


@dataclass
class Timeline:
    pieces: list[Piece]
    duration: float                     # durasi video hasil, tanpa end card
    cut_seconds: float = 0.0
    end_card_s: float = 0.0
    notes: list[str] = field(default_factory=list)

    @property
    def total(self) -> float:
        return self.duration + self.end_card_s

    def map_all(self, src_t: float) -> list[float]:
        return [p.out_s + (src_t - p.src_s) for p in self.pieces if p.src_s <= src_t < p.src_e]

    def map_time(self, src_t: float) -> float:
        """Waktu hasil untuk waktu klip asli. Teaser dilewati bila ada kemunculan utama;
        waktu yang terpotong di-snap ke potongan terdekat."""
        main = [p for p in self.pieces if not p.teaser and p.src_s <= src_t < p.src_e]
        if main:
            p = main[0]
            return p.out_s + (src_t - p.src_s)
        hits = self.map_all(src_t)
        if hits:
            return hits[0]
        p = min((p for p in self.pieces if not p.teaser) or self.pieces,
                key=lambda p: min(abs(src_t - p.src_s), abs(src_t - p.src_e)))
        return p.out_s if src_t < p.src_s else max(p.out_s, p.out_e - 0.01)

    def piece_at(self, out_t: float) -> Piece:
        for p in self.pieces:
            if p.out_s <= out_t < p.out_e:
                return p
        return self.pieces[-1]


def _merge(intervals: list[tuple[float, float]]) -> list[tuple[float, float]]:
    out: list[list[float]] = []
    for s, e in sorted(i for i in intervals if i[1] > i[0]):
        if out and s <= out[-1][1]:
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([s, e])
    return [(a, b) for a, b in out]


def _complement(cuts: list[tuple[float, float]], dur: float) -> list[tuple[float, float]]:
    kept, t = [], 0.0
    for s, e in cuts:
        if s > t:
            kept.append((t, s))
        t = max(t, e)
    if t < dur:
        kept.append((t, dur))
    return kept


def _norm(w: str) -> str:
    return w.lower().strip(".,!?;:\"'…-")


def build_timeline(ctx: dict, plan: dict, max_cut_ratio: float = 0.30) -> Timeline:
    words = ctx["words"]
    dur = float(ctx["duration"])
    fps = float(ctx["fps"])
    cuts_cfg = plan.get("cuts") or {}
    notes: list[str] = []

    def silence_cuts() -> list[tuple[float, float]]:
        if not words:
            return []
        out = []
        if words[0]["s"] > EDGE_PAD_S + 0.1:
            out.append((0.0, words[0]["s"] - EDGE_PAD_S))
        for a, b in zip(words, words[1:]):
            if b["s"] - a["e"] > SILENCE_MIN_GAP_S:
                out.append((a["e"] + EDGE_PAD_S, b["s"] - EDGE_PAD_S))
        if dur - words[-1]["e"] > EDGE_PAD_S + 0.4:
            out.append((words[-1]["e"] + EDGE_PAD_S + 0.25, dur))
        return out

    def word_span_cut(a: int, b: int) -> tuple[float, float]:
        # Jangan sampai memakan kata tetangga
        lo = words[a]["s"] - 0.03
        hi = words[b]["e"] + 0.03
        if a > 0:
            lo = max(lo, words[a - 1]["e"] + 0.01)
        if b + 1 < len(words):
            hi = min(hi, words[b + 1]["s"] - 0.01)
        return lo, hi

    def filler_cuts():
        return [word_span_cut(w["i"], w["i"]) for w in words if _norm(w["w"]) in FILLERS]

    def extra_cuts():
        return [word_span_cut(r["from_word"], r["to_word"]) for r in cuts_cfg.get("ranges") or []]

    layers = []
    if cuts_cfg.get("remove_silence"):
        layers.append(("jeda", silence_cuts()))
    if cuts_cfg.get("remove_fillers"):
        layers.append(("kata pengisi", filler_cuts()))
    if cuts_cfg.get("ranges"):
        layers.append(("potongan Gemini", extra_cuts()))

    # Kalau total potongan melebihi batas, buang lapisan paling "berani" dulu
    while True:
        cuts = _merge([c for _, layer in layers for c in layer])
        removed = sum(e - s for s, e in cuts)
        if removed <= dur * max_cut_ratio or not layers:
            break
        name, _ = layers.pop()
        notes.append(f"potongan {name} dibatalkan: total potongan melebihi {int(max_cut_ratio * 100)}%")

    kept = _complement(cuts, dur)

    ranges: list[tuple[float, float, bool]] = []
    teaser = plan.get("hook_teaser") or {}
    if teaser.get("enabled") and words:
        a, b = words[teaser["from_word"]], words[teaser["to_word"]]
        ts, te = max(0.0, a["s"] - 0.05), min(dur, b["e"] + 0.15)
        for ks, ke in kept:
            s, e = max(ks, ts), min(ke, te)
            if e > s:
                ranges.append((s, e, True))
    ranges += [(s, e, False) for s, e in kept]

    shots = ctx["shots"]
    pieces: list[Piece] = []
    out_t = 0.0
    for s, e, is_teaser in ranges:
        for k, sh in enumerate(shots):
            ps, pe = max(s, sh["t_start"]), min(e, sh["t_end"])
            f_s, f_e = round(ps * fps), round(pe * fps)
            if f_e - f_s < MIN_PIECE_FRAMES:
                continue
            length = (f_e - f_s) / fps
            pieces.append(Piece(f_s / fps, f_e / fps, out_t, out_t + length, k, is_teaser))
            out_t += length

    end_card = plan.get("end_card") or {}
    return Timeline(
        pieces=pieces,
        duration=out_t,
        cut_seconds=round(sum(e - s for s, e in cuts), 2),
        end_card_s=float(end_card.get("duration_s", 0)) if end_card.get("enabled") else 0.0,
        notes=notes,
    )


def output_words(ctx: dict, tl: Timeline) -> list[dict]:
    """Kata dalam urutan video hasil (kata di teaser muncul dua kali), dengan waktu hasil."""
    sid_of: dict[int, int] = {}
    for k, sen in enumerate(ctx["sentences"]):
        for i in range(sen["from_word"], sen["to_word"] + 1):
            sid_of[i] = k
    out = []
    for p in tl.pieces:
        for w in ctx["words"]:
            mid = (w["s"] + w["e"]) / 2
            if p.src_s <= mid < p.src_e:
                s = p.out_s + max(0.0, w["s"] - p.src_s)
                e = p.out_s + min(p.src_e, w["e"]) - p.src_s
                out.append({"i": w["i"], "w": w["w"], "s": round(s, 3), "e": round(max(e, s + 0.05), 3),
                            "sid": (sid_of.get(w["i"], -1), p.teaser)})
    return out
