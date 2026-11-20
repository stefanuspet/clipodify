"""Normalisasi EditPlan: nomor kata → detik, paksa batas, catat setiap koreksi.

Tidak ada persetujuan manusia sebelum render, jadi semua output Gemini lewat sini.
Hasilnya selalu bisa dirender, apa pun isi plan mentahnya.
"""

from __future__ import annotations

import re

from backend.editing.plan_schema import FRAMINGS, INTENTS, PERSON_ROLES, POSITIONS, TEXT_ROLES

MAX_TEXT_CHARS = 64
MAX_BEATS = 24
MAX_CUT_RANGES = 6
MAX_CUT_RANGE_WORDS = 30
PROTECTED_BEATS = {"punchline", "key_line", "twist", "payoff", "laugh"}   # tidak boleh dipotong

_HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")
GENERIC_QUESTION = re.compile(r"(setuju (gak|nggak|ga|engga)|gimana menurut (kalian|kamu)|bagaimana menurut)",
                              re.IGNORECASE)


def _clamp(v, lo, hi, default):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, v))


def validate_plan(raw: dict, ctx: dict, cat: dict, limits: dict | None = None) -> tuple[dict, list[str]]:
    from backend.editing.persona import DEFAULT_LIMITS
    lim = {**DEFAULT_LIMITS, **(limits or {})}
    fixes: list[str] = []
    words = ctx["words"]
    dur = float(ctx["duration"])
    last = len(words) - 1

    def word(i, field_name: str):
        try:
            i = int(i)
        except (TypeError, ValueError):
            fixes.append(f"{field_name}: nomor kata tidak valid ({i!r}) → #0")
            i = 0
        if i < 0 or i > last:
            j = max(0, min(last, i))
            fixes.append(f"{field_name}: #{i} di luar transkrip → #{j}")
            i = j
        return words[i] if words else {"i": 0, "s": 0.0, "e": 0.0, "w": ""}

    def color(v, default, field_name):
        if isinstance(v, str) and _HEX.match(v.strip()):
            return v.strip().upper()
        fixes.append(f"{field_name}: warna {v!r} tidak valid → {default}")
        return default

    def pick(v, allowed, default, field_name):
        if v in allowed:
            return v
        fixes.append(f"{field_name}: {v!r} tidak tersedia → {default!r}")
        return default

    # --- people (nama tiap identitas wajah; hanya ID yang benar-benar ada)
    known = {p["id"] for p in ctx.get("people") or []}
    people = []
    for k, p in enumerate(raw.get("people") or []):
        if p.get("id") not in known or any(x["id"] == p["id"] for x in people):
            fixes.append(f"people[{k}]: id {p.get('id')!r} tidak dikenal/ganda → dibuang")
            continue
        people.append({"id": p["id"], "name": " ".join(str(p.get("name") or "").split())[:60] or p["id"],
                       "role": p.get("role") if p.get("role") in PERSON_ROLES else "unknown",
                       "confidence": _clamp(p.get("confidence"), 0.0, 1.0, 0.0),
                       "evidence": str(p.get("evidence") or "")[:200]})

    # --- beats (analisis momen dari Gemini; dipakai untuk aturan SFX & disimpan untuk belajar)
    beats = []
    for k, b in enumerate((raw.get("beats") or [])[:MAX_BEATS]):
        if b.get("type") not in cat["beat_types"]:
            fixes.append(f"beats[{k}]: tipe {b.get('type')!r} tidak dikenal → dibuang")
            continue
        a = word(b.get("from_word"), f"beats[{k}].from_word")
        z = word(b.get("to_word"), f"beats[{k}].to_word")
        if z["i"] < a["i"]:
            a, z = z, a
        beats.append({"type": b["type"], "from_word": a["i"], "to_word": z["i"],
                      "s": a["s"], "e": z["e"], "note": str(b.get("note") or "")[:200]})

    out: dict = {
        "people": people,
        "beats": beats,
        "intent": pick(raw.get("intent"), INTENTS, "story", "intent"),
        "reasoning": str(raw.get("reasoning") or "")[:800],
        "framing": pick(raw.get("framing"), FRAMINGS, "grid_auto", "framing"),
    }

    # --- subtitle: resep dari Gemini (biasanya turunan gaya favorit Studio)
    from backend.editing.subtitle_style import validate_recipe
    sub = raw.get("subtitle") or {}
    styles = {x["id"]: x for x in cat["styles"]}
    base = styles.get(sub.get("base_style_id"))
    recipe_raw = sub.get("recipe") or (base or {}).get("recipe")
    if not recipe_raw:
        fixes.append("subtitle.recipe: kosong → pakai gaya default")
    families = [f["family"] for f in cat["font_families"]]
    recipe = validate_recipe(recipe_raw, fixes, "subtitle.recipe")
    if recipe["font"]["family"] not in families:
        fixes.append(f"subtitle.recipe.font: {recipe['font']['family']!r} tidak ada di daftar → Anton")
        recipe["font"] = {"family": "Anton", "weight": "400"}
    hl: list[int] = []
    for v in sub.get("highlight_words") or []:
        try:
            v = int(v)
        except (TypeError, ValueError):
            continue
        if 0 <= v <= last and v not in hl:
            hl.append(v)
    cap = max(3, len(words) // max(1, int(lim["highlight_every_n_words"])))
    if len(hl) > cap:
        fixes.append(f"subtitle.highlight_words: {len(hl)} kata → dibatasi {cap}")
        hl = hl[:cap]
    out["subtitle"] = {"enabled": bool(sub.get("enabled", True)) and bool(words),
                       "base_style_id": sub.get("base_style_id") if base else "baru",
                       "recipe": recipe, "highlight_words": sorted(hl)}

    # --- grade: resep warna (bukan preset)
    g = raw.get("grade") or {}
    grade = {
        "contrast": _clamp(g.get("contrast"), 0.85, 1.4, 1.0),
        "saturation": _clamp(g.get("saturation"), 0.6, 1.5, 1.0),
        "brightness": _clamp(g.get("brightness"), -0.1, 0.1, 0.0),
        "gamma": _clamp(g.get("gamma"), 0.8, 1.2, 1.0),
        "warmth": _clamp(g.get("warmth"), -1.0, 1.0, 0.0),
        "tint": _clamp(g.get("tint"), -1.0, 1.0, 0.0),
    }
    # Simulasikan grade di frame sampel; kurangi bila merusak (highlight/kulit/saturasi)
    from backend.editing.color_analysis import check_grade, check_subtitle_contrast
    try:
        grade, gfix = check_grade(ctx, grade)
        fixes += gfix
    except Exception as e:  # analisis gagal tidak boleh menggagalkan render
        fixes.append(f"simulasi grade dilewati: {e}")
    out["grade"] = grade
    if out["subtitle"]["enabled"]:
        out["subtitle"]["recipe"], cfix = check_subtitle_contrast(ctx.get("colors") or [], out["subtitle"]["recipe"])
        fixes += cfix

    # --- SFX: tag → file, ke detik, urutkan, paksa jarak & kepadatan
    sfx_ids = {s["id"]: s for s in cat["sfx"]}
    tag_files = {t["tag"]: t["files"] for t in cat["sfx_tags"]}
    cands = []
    for k, item in enumerate(raw.get("sfx") or []):
        tag = item.get("tag")
        if tag not in tag_files:
            fixes.append(f"sfx[{k}]: tag {tag!r} tidak ada → dibuang")
            continue
        w = word(item.get("word_index"), f"sfx[{k}].word_index")
        sid = None                          # file dipilih setelah diurutkan (giliran per tag)
        edge = "start" if item.get("edge") == "start" else "end"
        t = w["s" if edge == "start" else "e"] + _clamp(item.get("offset_s"), -2.0, 0.5, 0.05)
        longest = max(sfx_ids[f]["duration_s"] for f in tag_files[tag])
        t = max(0.0, min(dur - longest * 0.3, t))
        cands.append({
            "sfx_id": sid, "tag": tag, "t": round(t, 3),
            "volume": _clamp(item.get("volume"), 0.2, 1.3, 0.7),
            "word": w["i"], "why": str(item.get("why") or "")[:200],
        })
    cands.sort(key=lambda x: x["t"])
    # Giliran varian per tag sesuai urutan muncul: SFX bertag sama tidak memakai file yang sama berturut-turut
    turn: dict[str, int] = {}
    for c in cands:
        files = tag_files[c["tag"]]
        c["sfx_id"] = files[turn.get(c["tag"], 0) % len(files)]
        turn[c["tag"]] = turn.get(c["tag"], 0) + 1
    quiet = [b for b in beats if b["type"] in set(lim["no_sfx_in_beats"])]
    kept: list[dict] = []
    for c in cands:
        hit = next((b for b in quiet if b["s"] - 0.1 <= c["t"] <= b["e"] + 0.3), None)
        if hit:
            fixes.append(f"sfx {c['sfx_id']} @#{c['word']}: jatuh di beat '{hit['type']}' → dibuang")
            continue
        if kept and c["t"] - kept[-1]["t"] < lim["min_sfx_gap_s"]:
            fixes.append(f"sfx {c['sfx_id']} @#{c['word']}: terlalu dekat SFX sebelumnya → dibuang")
            continue
        if len([x for x in kept if c["t"] - x["t"] < 10.0]) >= lim["max_sfx_per_10s"]:
            fixes.append(f"sfx {c['sfx_id']} @#{c['word']}: melebihi {lim['max_sfx_per_10s']}/10 detik → dibuang")
            continue
        kept.append(c)
    out["sfx"] = kept

    # --- text overlays
    texts = []
    for k, t in enumerate(raw.get("text_overlays") or []):
        text = " ".join(str(t.get("text") or "").split())
        if not text:
            fixes.append(f"text[{k}]: kosong → dibuang")
            continue
        if len(texts) >= lim["max_text_overlays"]:
            fixes.append(f"text[{k}]: melebihi {lim['max_text_overlays']} teks → dibuang")
            continue
        if len(text) > MAX_TEXT_CHARS:
            # Potong di batas kata + elipsis; lebih baik daripada kalimat terpotong tanpa tanda
            fixes.append(f"text[{k}]: terlalu panjang ({len(text)} karakter) → dipotong")
            text = text[:MAX_TEXT_CHARS].rsplit(" ", 1)[0].rstrip(",.:;") + "…"
        w = word(t.get("start_word"), f"text[{k}].start_word")
        start = w["s"]
        length = _clamp(t.get("duration_s"), 1.0, 6.0, 3.0)
        if start + length > dur:
            start = max(0.0, dur - length)
        texts.append({
            "text": text,
            "role": pick(t.get("role"), TEXT_ROLES, "emphasis", f"text[{k}].role"),
            "start": round(start, 3), "duration": round(length, 3),
            "position": pick(t.get("position"), POSITIONS, "top", f"text[{k}].position"),
            "color": color(t.get("color"), "#FFFFFF", f"text[{k}].color"),
            "font": pick(t.get("font"), [f["family"] for f in cat["font_families"]], "Anton", f"text[{k}].font"),
        })
    out["text_overlays"] = texts

    # --- effects (time-gated color effects)
    effects = []
    for k, e in enumerate(raw.get("effects") or []):
        if len(effects) >= lim["max_effects"]:
            fixes.append(f"effects[{k}]: melebihi {lim['max_effects']} → dibuang")
            continue
        typ = e.get("type")
        if typ not in cat["effects"]:
            fixes.append(f"effects[{k}]: {typ!r} tidak ada → dibuang")
            continue
        a = word(e.get("from_word"), f"effects[{k}].from_word")
        b = word(e.get("to_word"), f"effects[{k}].to_word")
        s, en = min(a["s"], b["s"]), max(a["e"], b["e"])
        if en - s < 0.3:
            en = min(dur, s + 0.6)
        if en - s > 8.0:
            fixes.append(f"effects[{k}]: terlalu panjang → dipotong 8 detik")
            en = s + 8.0
        effects.append({"type": typ, "start": round(s, 3), "end": round(en, 3)})
    out["effects"] = effects

    # --- cuts (dipakai timeline; batas total potongan dicek di timeline)
    c = raw.get("cuts") or {}
    ranges = []
    for k, r in enumerate((c.get("ranges") or [])[:MAX_CUT_RANGES]):
        a = word(r.get("from_word"), f"cuts.ranges[{k}].from_word")["i"]
        z = word(r.get("to_word"), f"cuts.ranges[{k}].to_word")["i"]
        a, z = min(a, z), max(a, z)
        if z - a + 1 > MAX_CUT_RANGE_WORDS:
            fixes.append(f"cuts.ranges[{k}]: {z - a + 1} kata terlalu panjang untuk dipotong → dibatalkan")
            continue
        hit = next((b for b in beats if b["type"] in PROTECTED_BEATS
                    and not (z < b["from_word"] or a > b["to_word"])), None)
        if hit:
            fixes.append(f"cuts.ranges[{k}]: mengenai beat '{hit['type']}' → dibatalkan")
            continue
        ranges.append({"from_word": a, "to_word": z, "why": str(r.get("why") or "")[:200]})
    out["cuts"] = {"remove_silence": bool(c.get("remove_silence")),
                   "remove_fillers": bool(c.get("remove_fillers")), "ranges": ranges}

    # --- hook teaser (flash-forward di depan; bagian aslinya tetap di tempat)
    h = raw.get("hook_teaser") or {}
    teaser = {"enabled": False, "from_word": 0, "to_word": 0}
    if h.get("enabled") and words:
        a = word(h.get("from_word"), "hook_teaser.from_word")
        z = word(h.get("to_word"), "hook_teaser.to_word")
        if z["i"] < a["i"]:
            a, z = z, a
        length = z["e"] - a["s"]
        if a["s"] < 3.0:
            fixes.append("hook_teaser: momennya sudah di 3 detik pertama → tidak perlu teaser")
        elif not (1.2 <= length <= 7.0):
            fixes.append(f"hook_teaser: durasi {length:.1f} detik di luar 1.2–7 detik → dibatalkan")
        else:
            teaser = {"enabled": True, "from_word": a["i"], "to_word": z["i"]}
    out["hook_teaser"] = teaser

    # --- zoom (waktu klip asli; renderer membuang zoom di shot multi-orang)
    zooms: list[dict] = []
    for k, zr in enumerate(raw.get("zooms") or []):
        a = word(zr.get("from_word"), f"zooms[{k}].from_word")
        z = word(zr.get("to_word"), f"zooms[{k}].to_word")
        s0, e0 = min(a["s"], z["s"]), max(a["e"], z["e"])
        style = "slow" if zr.get("style") == "slow" else "punch"
        e0 = min(e0, s0 + (8.0 if style == "slow" else 2.5))
        if e0 - s0 < 0.4:
            e0 = s0 + 0.6
        # Cek jumlah orang di shot DI SINI, sebelum menghitung jatah zoom — kalau tidak,
        # zoom yang pasti dibuang ikut menghabiskan jatah zoom yang valid.
        shot = next((sh for sh in ctx["shots"] if sh["t_start"] <= s0 < sh["t_end"]), None)
        n_people = len([x for x in (shot or {}).get("persons", []) if x != "_none"])
        if out["framing"] != "grid_auto" or n_people != 1:
            fixes.append(f"zooms[{k}]: shot berisi {n_people} orang (zoom hanya untuk 1 orang) → dibuang")
            continue
        if len([x for x in zooms if abs(x["s"] - s0) < 10.0]) >= lim["max_zoom_per_10s"]:
            fixes.append(f"zooms[{k}]: melebihi {lim['max_zoom_per_10s']}/10 detik → dibuang")
            continue
        zooms.append({"s": round(s0, 3), "e": round(e0, 3), "style": style,
                      "scale": _clamp(zr.get("scale"), 1.04, 1.30, 1.12)})
    out["zooms"] = zooms

    # --- build-up: riser yang berakhir tepat di kata + hit (8A-3)
    riser = {x.get("length"): x for x in cat["sfx"] if x.get("tag") == "riser"}
    quiet_set = set(lim["no_sfx_in_beats"])
    build_ups: list[dict] = []
    for k, bu in enumerate(raw.get("build_ups") or []):
        if len(build_ups) >= int(lim.get("max_build_ups", 2)):
            fixes.append(f"build_ups[{k}]: melebihi {lim.get('max_build_ups', 2)} per klip → dibuang")
            continue
        if not riser:
            fixes.append("build_ups: file riser tidak ada → dibuang")
            break
        w = word(bu.get("to_word"), f"build_ups[{k}].to_word")
        hit = next((b for b in beats if b["type"] in quiet_set and b["s"] - 0.1 <= w["s"] <= b["e"] + 0.3), None)
        if hit:
            fixes.append(f"build_ups[{k}]: kata #{w['i']} di beat '{hit['type']}' → dibuang")
            continue
        length = "long" if bu.get("length") == "long" else "short"
        if length not in riser:
            length = next(iter(riser))
        if w["s"] - riser[length]["duration_s"] < 0.2:
            if length == "long" and "short" in riser and w["s"] - riser["short"]["duration_s"] >= 0.2:
                fixes.append(f"build_ups[{k}]: ruang sebelum kata #{w['i']} kurang → riser pendek")
                length = "short"
            else:
                fixes.append(f"build_ups[{k}]: kata #{w['i']} terlalu dekat awal klip → dibuang")
                continue
        if any(abs(w["s"] - x["s"]) < 8.0 for x in build_ups):
            fixes.append(f"build_ups[{k}]: terlalu dekat build-up lain (<8 dtk) → dibuang")
            continue
        build_ups.append({"word": w["i"], "s": w["s"], "length": length,
                          "riser_id": riser[length]["id"], "riser_s": riser[length]["duration_s"],
                          "hit": bu.get("hit") if bu.get("hit") in ("impact", "bass_drop") else None,
                          "why": str(bu.get("why") or "")[:200]})
    out["build_ups"] = build_ups
    # SFX biasa yang bertabrakan dengan build-up dibuang (build-up adalah momen utamanya)
    keep_sfx = []
    for x in out["sfx"]:
        clash = next((b for b in build_ups if b["s"] - b["riser_s"] - 0.3 <= x["t"] <= b["s"] + 1.5), None)
        if clash:
            fixes.append(f"sfx {x['sfx_id']} @#{x['word']}: bertabrakan dengan build-up di #{clash['word']} → dibuang")
        else:
            keep_sfx.append(x)
    out["sfx"] = keep_sfx

    # --- emoji stiker (8D-2)
    from backend.editing.visuals import EMOJI_SET, LAUGH_EMOJI
    emojis: list[dict] = []
    for k, e in enumerate(raw.get("emojis") or []):
        if len(emojis) >= int(lim.get("max_emojis", 3)):
            fixes.append(f"emojis[{k}]: melebihi {lim.get('max_emojis', 3)} per klip → dibuang")
            continue
        if e.get("emoji") not in EMOJI_SET:
            fixes.append(f"emojis[{k}]: {e.get('emoji')!r} tidak tersedia → dibuang")
            continue
        w = word(e.get("word_index"), f"emojis[{k}].word_index")
        sad = next((b for b in beats if b["type"] == "emotional_peak" and b["s"] - 0.2 <= w["s"] <= b["e"] + 0.3), None)
        if sad and e["emoji"] in LAUGH_EMOJI:
            fixes.append(f"emojis[{k}]: emoji tawa di momen haru → dibuang")
            continue
        if any(abs(w["s"] - x["s"]) < 4.0 for x in emojis):
            fixes.append(f"emojis[{k}]: terlalu dekat emoji lain (<4 dtk) → dibuang")
            continue
        emojis.append({"emoji": e["emoji"], "word": w["i"], "s": w["s"], "why": str(e.get("why") or "")[:160]})
    out["emojis"] = emojis
    out["transition_visual"] = lim.get("transition_visual", "zoom") if lim.get("transition_visual") in (
        "zoom", "flash", "mix", "none") else "zoom"
    out["name_labels"] = bool(lim.get("name_labels", True))
    out["progress_bar"] = bool(lim.get("progress_bar", True))
    out["music_follow_beats"] = bool(lim.get("music_follow_beats", True))

    # --- musik: hanya bila ada lagu berlisensi di library
    m = raw.get("music") or {}
    music = {"enabled": bool(m.get("enabled")),
             "mood": pick(m.get("mood"), cat["music_moods"], "chill", "music.mood"),
             "energy": m.get("energy") if m.get("energy") in ("low", "mid", "high") else "mid",
             "volume_db": _clamp(m.get("volume_db"), -28.0, -12.0, -20.0)}
    if music["enabled"] and not cat["music_available"]:
        fixes.append("music: library musik berlisensi masih kosong → tanpa musik")
        music["enabled"] = False
    out["music"] = music

    # --- end card (frame terakhir dibekukan + digelapkan + teks)
    ec = raw.get("end_card") or {}
    text = " ".join(str(ec.get("text") or "").split())
    end_card = {"enabled": bool(ec.get("enabled")) and bool(text),
                "text": text[:MAX_TEXT_CHARS], "duration_s": _clamp(ec.get("duration_s"), 1.5, 3.5, 2.5)}
    if ec.get("enabled") and not text:
        fixes.append("end_card: teks kosong → dimatikan")
    out["end_card"] = end_card
    out["transition_sfx"] = bool(lim.get("transition_sfx", True))
    out["fill_gaps"] = bool(lim.get("fill_gaps", True)) and out["intent"] != "emotional"
    if end_card["enabled"]:
        before = len(out["text_overlays"])
        out["text_overlays"] = [t for t in out["text_overlays"]
                                if not (t["role"] == "question" and t["start"] > dur - 8.0)]
        if len(out["text_overlays"]) < before:
            fixes.append("text question di akhir dibuang: sudah ada pertanyaan di end card")

    # Pertanyaan generik tidak bisa ditulis ulang otomatis, tapi dicatat supaya terlihat
    asks = [t["text"] for t in out["text_overlays"] if t["role"] == "question"]
    asks += [end_card["text"]] if end_card["enabled"] else []
    for q in asks:
        if GENERIC_QUESTION.search(q):
            fixes.append(f"pertanyaan generik (melanggar persona): {q!r}")

    return out, fixes
