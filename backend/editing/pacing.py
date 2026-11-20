"""Tempo: build-up (riser → hit, 8A-3) dan pengisi celah panjang (8A-4). Tanpa panggilan Gemini.

Semua dihitung di waktu video HASIL, setelah potong jeda/teaser, supaya ujung riser dan
puncak hit jatuh tepat di kata walaupun ada potongan di depannya.
"""

from __future__ import annotations

from backend.editing.rhythm import effective_t, measure

_STOP = {"dan", "yang", "itu", "ini", "di", "ke", "dari", "dengan", "untuk", "pada", "atau", "tapi",
         "karena", "jadi", "sudah", "akan", "bisa", "ada", "saya", "aku", "kamu", "dia", "kita",
         "kami", "mereka", "tidak", "juga", "ya", "iya", "nah", "gitu", "loh", "sih", "deh", "nih",
         "tuh", "kan", "aja", "saja", "lagi", "mau", "harus", "terus", "gak", "nggak", "ne"}
_FILL_ZOOM = 1.08
_FILL_SFX_TAGS = ("tap", "pop", "tick")
_FILL_SFX_VOLUME = 0.4
_FILL_PER_S = 1 / 12        # jatah pengisi: kira-kira 1 per 12 detik video


def _hit_file(tag: str | None, sfx: list[dict]) -> dict | None:
    if not tag:
        return None
    return next((x for x in sfx if x["tag"] == tag), None)


def build_up_sfx(plan: dict, tl) -> tuple[list[dict], list[dict]]:
    """Riser (akhir file = awal kata) + hit (puncak = awal kata), di waktu hasil."""
    from backend.renderer.sfx import list_sfx
    files = list_sfx()
    by_id = {x["id"]: x for x in files}
    items, report = [], []
    for bu in plan.get("build_ups") or []:
        target = tl.map_time(bu["s"])
        r = by_id.get(bu["riser_id"])
        if not r:
            continue
        start = target - r["duration_s"]
        if start < 0:
            report.append({"t": round(target, 2), "placed": False, "reason": "riser melewati awal video"})
            continue
        items.append({"sfx_id": r["id"], "t": round(start, 3), "volume": 0.55, "word": bu["word"],
                      "why": f"build-up: {bu['why']}", "group": "build_up", "kind": "build_up",
                      "align_t": target})
        h = _hit_file(bu.get("hit"), files)
        if h:
            items.append({"sfx_id": h["id"], "t": round(target - h["peak_s"], 3),
                          "volume": 0.7 if h["tag"] == "bass_drop" else 0.8, "word": bu["word"],
                          "why": "hit build-up", "group": "build_up", "kind": "build_up", "align_t": target})
        report.append({"t": round(target, 2), "placed": True, "riser": r["id"], "hit": h["id"] if h else None})
    return items, report


def _in_quiet(src_t: float, plan: dict, quiet: set[str]) -> bool:
    return any(b["type"] in quiet and b["s"] - 0.2 <= src_t <= b["e"] + 0.3 for b in plan.get("beats") or [])


def fill_gaps(ctx: dict, tl, plan: dict, zooms: list[dict], sfx: list[dict], texts: list[dict],
              effects: list[dict], limits: dict) -> tuple[list[dict], list[dict], list[dict]]:
    """Isi celah > max_gap_s dengan zoom kecil (shot 1 orang) atau SFX ringan di kata terkuat.

    Return (zooms baru [waktu klip asli], sfx baru [waktu hasil], laporan).
    """
    from backend.editing.timeline import output_words
    from backend.renderer.sfx import list_sfx

    if not plan.get("fill_gaps"):
        return [], [], []
    max_gap = float(limits.get("max_gap_s", 6.0))
    quiet = set(limits.get("no_sfx_in_beats") or []) | {"emotional_peak"}
    gap_sfx = limits.get("min_sfx_gap_s", 1.5)
    hl = set((plan.get("subtitle") or {}).get("highlight_words") or [])
    words = [w for w in output_words(ctx, tl) if not w["sid"][1]]          # tanpa kata di teaser
    src_of = {w["i"]: w for w in ctx["words"]}
    fill_files = [x for x in list_sfx() if x["tag"] in _FILL_SFX_TAGS]
    new_z, new_s, report, tried = [], [], [], set()

    max_fills = max(3, int(tl.total * _FILL_PER_S))
    for _ in range(max_fills * 2):
        if len(new_z) + len(new_s) >= max_fills:
            break
        r = measure(tl, ctx, zooms + new_z, sfx + new_s, texts, effects)
        gap = next((g for g in r["gaps"] if g["len"] > max_gap and (g["s"], g["e"]) not in tried), None)
        if not gap:
            break
        tried.add((gap["s"], gap["e"]))
        mid = (gap["s"] + gap["e"]) / 2
        cands = []
        for w in words:
            if not (gap["s"] + 1.2 <= w["s"] <= gap["e"] - 1.2):
                continue
            src = src_of[w["i"]]
            if _in_quiet(src["s"], plan, quiet):
                continue
            clean = w["w"].lower().strip(".,!?;:\"'…-")
            if clean in _STOP or len(clean) < 4:
                continue
            score = (3.0 if w["i"] in hl else 0.0) + min(len(clean), 10) * 0.1 - abs(w["s"] - mid) / max(gap["len"], 1)
            cands.append((score, w, src))
        entry = {"gap": [gap["s"], gap["e"]], "len": gap["len"]}
        if not cands:
            entry["action"] = "dibiarkan (momen tenang / tidak ada kata yang cocok)"
            report.append(entry)
            continue
        # Utamakan kata yang membelah celah jadi dua bagian yang sama-sama ≤ batas
        splitting = [c for c in cands if c[1]["s"] - gap["s"] <= max_gap and gap["e"] - c[1]["s"] <= max_gap]
        _, w, src = max(splitting or cands, key=lambda c: c[0])
        piece = tl.piece_at(w["s"])
        shot = ctx["shots"][piece.shot]
        n_people = len([p for p in shot["persons"] if p != "_none"])
        zoom_ok = (plan.get("framing") == "grid_auto" and n_people == 1
                   and all(abs(z["s"] - src["s"]) >= 10.0 for z in zooms + new_z))
        if zoom_ok:
            new_z.append({"s": round(src["s"], 3), "e": round(min(src["e"] + 0.5, piece.src_e), 3),
                          "style": "punch", "scale": _FILL_ZOOM, "fill": True})
            entry.update(action="zoom", word=w["w"], t=round(w["s"], 2))
        elif fill_files and all(abs(effective_t(x) - w["e"]) >= gap_sfx for x in sfx + new_s):
            f = fill_files[len(new_s) % len(fill_files)]
            new_s.append({"sfx_id": f["id"], "t": round(max(0.0, w["e"] - f["peak_s"]), 3),
                          "volume": _FILL_SFX_VOLUME, "word": w["i"], "why": "isi celah", "auto": True,
                          "peak_s": f["peak_s"], "kind": "isi_celah"})
            entry.update(action=f"sfx {f['tag']}", word=w["w"], t=round(w["e"], 2))
        else:
            entry["action"] = "dibiarkan (zoom & SFX tidak memungkinkan)"
        report.append(entry)
    return new_z, new_s, report
