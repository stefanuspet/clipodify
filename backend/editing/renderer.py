"""Render EditPlan (sudah divalidasi) dalam SATU kali encode FFmpeg.

Graf video: potongan timeline (grid/crop per potongan) → zoom → end card (freeze)
            → grade → efek berwaktu → gelap end card → subtitle + teks (ASS)
Graf audio: suara (ikut potongan, fade 8ms di sambungan) → [musik di-duck oleh suara]
            + SFX → loudnorm.
Semua waktu di plan adalah waktu klip asli; dipetakan ke waktu hasil lewat Timeline.
"""

from __future__ import annotations

import bisect
import logging
import os
import re
import subprocess
import textwrap
from dataclasses import replace

from backend.editing.pacing import build_up_sfx, fill_gaps
from backend.editing.rhythm import effective_t, measure as measure_rhythm
from backend.editing.timeline import Timeline, build_timeline, output_words

logger = logging.getLogger(__name__)

_SUB_MAX_GAP_S = 0.35         # celah kecil antar chunk ditutup supaya tidak berkedip
_SUB_BREAK_GAP_S = 0.6        # celah lebih besar = baris baru
_TEXT_SIZE = {"hook_title": 78, "question": 68, "emphasis": 66, "cta": 60, "end_card": 84}
_TEXT_WRAP = {"hook_title": 18, "question": 22, "emphasis": 20, "cta": 22, "end_card": 16}
_TEXT_PREF_Y = {"top": 280, "center": 960, "bottom": 1300}
_AUDIO_JOIN_FADE_S = 0.008
_LOUDNESS = "loudnorm=I=-14:TP=-1.5:LRA=11"   # target umum TikTok/Reels/Shorts


# ------------------------------------------------------------------ video

def _framing_graph(ctx: dict, tl: Timeline, framing: str) -> tuple[str, str, str]:
    """Potongan timeline → grid/crop per potongan → concat. Return (graph, vlabel, alabel)."""
    from backend.analyzer.grid_composer import GridSegment, _build_filter
    from backend.editing.context import shots_to_segments

    shots = shots_to_segments(ctx["shots"])
    segs = []
    for p in tl.pieces:
        if framing == "grid_auto":
            segs.append(replace(shots[p.shot], t_start=p.src_s, t_end=p.src_e))
        else:
            segs.append(GridSegment(p.src_s, p.src_e, "single", ["_none"], {}))
    fc, _, _ = _build_filter(segs, ctx["width"], ctx["height"], ctx["fps"],
                             always_trim=True, audio_fade_s=_AUDIO_JOIN_FADE_S)
    return fc, "[out]", "[aout]"


def _zoom_filter(ctx: dict, tl: Timeline, plan: dict, zones, fixes: list[str],
                 extra: list[tuple] | None = None) -> str | None:
    """zoompan berwaktu. Zoom rencana: hanya potongan 1 orang, mengarah ke wajah.
    `extra`: zoom 'settle' transisi (8D-1) — seluruh kanvas, mulai besar lalu kembali ke 1."""
    windows = []
    for z in plan["zooms"]:
        a = tl.map_time(z["s"])
        p = tl.piece_at(a)
        shot = ctx["shots"][p.shot]
        real = [x for x in shot["persons"] if x != "_none"]
        if plan["framing"] != "grid_auto" or len(real) != 1:
            fixes.append(f"zoom @{z['s']:.1f}s: shot berisi {len(real)} orang → dibuang")
            continue
        b = min(a + (z["e"] - z["s"]), p.out_e)
        faces = next((zn.faces for zn in zones if zn.t_start <= a < zn.t_end), [])
        cx, cy = 540, 760
        if faces:
            x0, y0, x1, y1 = faces[0]
            cx, cy = (x0 + x1) // 2, (y0 + y1) // 2
        windows.append((a, b, z["scale"], z["style"], cx, cy))
    for w in extra or []:
        if not any(a < w[1] and w[0] < b for a, b, *_ in windows):     # jangan menimpa zoom rencana
            windows.append(w)
    if not windows:
        return None

    def ramp(a, b, style):
        if style == "punch":
            return f"min(1,(it-{a:.3f})/0.10)"
        if style == "settle":
            return f"pow(1-(it-{a:.3f})/{max(b - a, 0.05):.3f},2)"
        return f"(it-{a:.3f})/{max(b - a, 0.1):.3f}"

    z_expr = "1" + "".join(
        f"+if(between(it,{a:.3f},{b:.3f}),{s - 1:.3f}*{ramp(a, b, st)},0)" for a, b, s, st, _, _ in windows)
    cx_expr, cy_expr = "540", "960"
    for a, b, _, _, cx, cy in reversed(windows):
        cx_expr = f"if(between(it,{a:.3f},{b:.3f}),{cx},{cx_expr})"
        cy_expr = f"if(between(it,{a:.3f},{b:.3f}),{cy},{cy_expr})"
    fps = ctx["fps"]
    # Upscale dulu supaya zoom pelan tidak "bergetar" karena pembulatan piksel zoompan
    return (f"scale=2160:3840:flags=lanczos,"
            f"zoompan=z='{z_expr}':x='max(0,min(iw-iw/zoom,({cx_expr})*2-iw/zoom/2))'"
            f":y='max(0,min(ih-ih/zoom,({cy_expr})*2-ih/zoom/2))':d=1:s=1080x1920:fps={fps:g},"
            # zoompan mengeluarkan timestamp kacau (tpad sesudahnya meledak jadi ribuan frame)
            f"setpts=N/FRAME_RATE/TB")


# ------------------------------------------------------------------ subtitle + teks (ASS)

def _subtitle_chunks(words: list[dict], max_chars: int = 999, words_per_line: int = 4) -> list[dict]:
    """Baris pendek (≤words_per_line kata, ≤max_chars) dari kata urutan hasil; putus di ganti kalimat/potongan."""
    chunks: list[list[dict]] = []
    cur: list[dict] = []
    for w in words:
        if cur and (w["sid"] != cur[-1]["sid"] or w["s"] - cur[-1]["e"] > _SUB_BREAK_GAP_S
                    or len(" ".join(x["w"] for x in cur + [w])) > max_chars):
            chunks.append(cur)
            cur = []
        cur.append(w)
        if len(cur) >= words_per_line or w["w"][-1:] in ".,!?;:":
            chunks.append(cur)
            cur = []
    if cur:
        chunks.append(cur)
    out = []
    for k, c in enumerate(chunks):
        start, end = c[0]["s"], c[-1]["e"]
        if k + 1 < len(chunks):
            nxt = chunks[k + 1][0]["s"]
            if 0 < nxt - end < _SUB_MAX_GAP_S:
                end = nxt
        out.append({"start_seconds": start, "end_seconds": max(end, start + 0.2),
                    "transcript_text": " ".join(w["w"] for w in c),
                    "words": [(w["w"], w["s"], w["e"]) for w in c],
                    "idx": [w["i"] for w in c]})
    return out


def _ass_color(hex_color: str) -> str:
    h = hex_color.lstrip("#")
    return f"{h[4:6]}{h[2:4]}{h[0:2]}".upper()


def _add_overlays(ass: str, texts: list[dict], zones, sub_bands: list[tuple]) -> str:
    """Teks overlay sebagai event ASS: kotak latar, pop-in, posisi menghindari wajah & subtitle."""
    from backend.editing.fonts import ensure_font
    from backend.editing.placement import choose_y
    from backend.editing.subtitle_style import _esc, _t as _seconds_to_ass_time

    if not texts:
        return ass
    style = ("Style: Overlay,Anton,66,&H00FFFFFF,&H00FFFFFF,&H55000000,&H00000000,"
             "0,0,0,0,100,100,0,0,3,16,0,5,40,40,0,1\n")
    if "Style: Overlay" not in ass:
        ass = ass.replace("\n[Events]", "\n" + style + "\n[Events]", 1)
    events = []
    for t in texts:
        font = ensure_font(t["font"], t.get("weight", "800"))
        size = _TEXT_SIZE.get(t["role"], 64)
        lines = textwrap.wrap(t["text"].upper(), _TEXT_WRAP.get(t["role"], 20)) or [t["text"].upper()]
        line_h = int(size * 1.22)
        h = line_h * len(lines) + 40
        w = int(max(len(x) for x in lines) * size * 0.56) + 70
        start, end = t["start"], t["start"] + t["duration"]
        avoid = [b for (bs, be, b) in sub_bands if bs < end and be > start]
        y = choose_y(zones, start, end, h, w, _TEXT_PREF_Y[t["position"]], avoid=avoid)
        tags = (f"\\an5\\pos(540,{y})\\fn{font['ass_name']}\\fs{size}\\1c&H{_ass_color(t['color'])}&"
                r"\fad(120,160)\fscx70\fscy70\t(0,140,\fscx106\fscy106)\t(140,230,\fscx100\fscy100)")
        body = "\\N".join(_esc(x) for x in lines)
        events.append(f"Dialogue: 2,{_seconds_to_ass_time(start)},{_seconds_to_ass_time(end)},"
                      f"Overlay,,0,0,0,,{{{tags}}}{body}")
    return ass.rstrip("\n") + "\n" + "\n".join(events) + "\n"


def _build_ass(ctx: dict, tl: Timeline, plan: dict, zones, texts: list[dict],
               labels: tuple[list[str], list[tuple]] = ([], [])) -> tuple[str | None, list[tuple]]:
    """Subtitle (dari resep gaya) + teks overlay dalam satu file ASS, posisi menghindari wajah."""
    from backend.editing.fonts import ensure_font
    from backend.editing.placement import PREFERRED_Y, band, choose_y
    from backend.editing.subtitle_style import (
        DEFAULT_RECIPE, build_events, build_header, line_metrics, max_chars,
    )

    sub = plan["subtitle"]
    label_events, label_bands = labels
    if not sub["enabled"] and not texts and not label_events:
        return None, []
    sub_bands: list[tuple] = []
    recipe = sub.get("recipe") or DEFAULT_RECIPE
    font = ensure_font(recipe["font"]["family"], recipe["font"]["weight"])
    ass = build_header(recipe, font["ass_name"])

    if sub["enabled"]:
        chunks = _subtitle_chunks(output_words(ctx, tl), max_chars(recipe), recipe["words_per_line"])
        box_h, _, char_w = line_metrics(recipe)
        ys: list[int] = []
        prev = None
        for c in chunks:
            w = int(len(c["transcript_text"]) * char_w) + 40
            avoid = [bd for (bs, be, bd) in label_bands if bs < c["end_seconds"] and be > c["start_seconds"]]
            y = choose_y(zones, c["start_seconds"], c["end_seconds"], box_h, w,
                         PREFERRED_Y[recipe["position"]], prev, avoid=avoid)
            ys.append(y)
            prev = y
            sub_bands.append((c["start_seconds"], c["end_seconds"], band(y, box_h, w)))
        ass += build_events(chunks, ys, recipe, set(sub.get("highlight_words") or []))

    ass = _add_overlays(ass, texts, zones, sub_bands + label_bands)
    if label_events:
        from backend.editing.fonts import ensure_font as _ef
        _ef("Montserrat", "900")
        if "Style: Overlay" not in ass:
            ass = ass.replace("\n[Events]", "\nStyle: Overlay,Anton,66,&H00FFFFFF,&H00FFFFFF,&H55000000,"
                              "&H00000000,0,0,0,0,100,100,0,0,1,4,0,5,40,40,0,1\n\n[Events]", 1)
        ass = ass.rstrip("\n") + "\n" + "\n".join(label_events) + "\n"
    return ass, sub_bands


def grade_filter(g: dict) -> str | None:
    """Resep grade → eq (+ colorbalance untuk hangat/dingin & tint). None bila netral."""
    neutral = {"contrast": 1.0, "saturation": 1.0, "brightness": 0.0, "gamma": 1.0, "warmth": 0.0, "tint": 0.0}
    if all(abs(g.get(k, v) - v) < 1e-3 for k, v in neutral.items()):
        return None
    f = (f"eq=contrast={g['contrast']:.3f}:saturation={g['saturation']:.3f}"
         f":brightness={g['brightness']:.3f}:gamma={g['gamma']:.3f}")
    w, t = g.get("warmth", 0.0), g.get("tint", 0.0)
    if abs(w) > 1e-3 or abs(t) > 1e-3:
        # hangat: merah naik/biru turun; tint: hijau (+) atau magenta (−)
        r, gr, b = 0.10 * w - 0.03 * t, 0.08 * t, -0.10 * w - 0.03 * t
        f += (f",colorbalance=rs={r:.3f}:gs={gr:.3f}:bs={b:.3f}"
              f":rm={r:.3f}:gm={gr:.3f}:bm={b:.3f}:rh={r * .6:.3f}:gh={gr * .6:.3f}:bh={b * .6:.3f}")
    return f


_TRANSITION_TAGS = ("whoosh", "swipe")
_TRANSITION_QUIET_BEATS = {"punchline"}   # + no_sfx_in_beats persona (tawa, puncak emosi)


def transition_whooshes(ctx: dict, tl: Timeline, plan: dict, existing: list[dict],
                        limits: dict) -> tuple[list[dict], list[dict]]:
    """8A-2: whoosh pelan di setiap cut kamera yang MENGGANTI layout grid.

    Puncak bunyi whoosh diselaraskan ke frame cut. Dilewati bila cut jatuh di beat
    tenang/penting atau terlalu dekat SFX lain. Return (sfx tambahan, laporan per cut).
    """
    from backend.renderer.sfx import list_sfx

    if not plan.get("transition_sfx") or plan.get("framing") != "grid_auto":
        return [], []
    files = [x for x in list_sfx() if x["tag"] in _TRANSITION_TAGS]
    if not files:
        return [], []
    quiet = set(limits.get("no_sfx_in_beats") or []) | _TRANSITION_QUIET_BEATS
    beats = [b for b in plan.get("beats") or [] if b["type"] in quiet]
    gap = limits.get("min_sfx_gap_s", 1.5)
    vol = float(limits.get("transition_sfx_volume", 0.45))
    added, report = [], []
    for a, b in zip(tl.pieces, tl.pieces[1:]):
        if a.teaser or b.teaser or a.shot == b.shot:
            continue
        la, lb = ctx["shots"][a.shot]["layout"], ctx["shots"][b.shot]["layout"]
        if la == lb:
            continue
        cut = b.out_s
        entry = {"t": round(cut, 2), "from": la, "to": lb, "placed": False}
        hit = next((x for x in beats if x["s"] - 0.1 <= b.src_s <= x["e"] + 0.3
                    or x["s"] - 0.1 <= a.src_e <= x["e"] + 0.3), None)
        near = next((s for s in existing + added if abs(effective_t(s) - cut) < gap), None)
        if hit:
            entry["reason"] = f"di beat '{hit['type']}'"
        elif near:
            entry["reason"] = f"dekat SFX {near['sfx_id']}"
        else:
            f = files[len(added) % len(files)]
            t = cut - f["peak_s"]
            if t < 0:
                entry["reason"] = "terlalu dekat awal video"
            else:
                added.append({"sfx_id": f["id"], "t": round(t, 3), "volume": vol, "word": -1,
                              "why": f"transisi {la} → {lb}", "auto": True, "peak_s": f["peak_s"],
                              "kind": "whoosh"})
                entry.update(placed=True, sfx=f["id"])
        report.append(entry)
    return added, report


def _output_zones(ctx: dict, tl: Timeline, framing: str):
    from backend.editing.placement import ShotZones, shot_zones
    base = shot_zones(ctx) if framing == "grid_auto" else [ShotZones(0, 0, [], []) for _ in ctx["shots"]]
    out = [ShotZones(p.out_s, p.out_e, base[p.shot].faces, base[p.shot].seams) for p in tl.pieces]
    if tl.end_card_s and tl.pieces:
        last = base[tl.pieces[-1].shot]
        out.append(ShotZones(tl.duration, tl.total, last.faces, last.seams))
    return out


# ------------------------------------------------------------------ main

def render_plan(ctx: dict, plan: dict, output_path: str, work_dir: str,
                limits: dict | None = None, seed: str = "", timeline: Timeline | None = None,
                fast: bool = False) -> dict:
    """Render plan ke output_path. Return info: fixes tambahan, durasi, potongan, musik.

    `timeline` dipakai preview Studio (jendela pendek); `fast` = encode cepat untuk preview.
    """
    from backend.editing.fonts import FONT_DIR
    from backend.editing.music import credit, pick_track
    from backend.renderer.filters import _build_expression_vf
    from backend.renderer.sfx import _sfx_path, list_sfx, norm_filter

    fixes: list[str] = []
    tl = timeline or build_timeline(ctx, plan, max_cut_ratio=(limits or {}).get("max_cut_ratio", 0.30))
    fixes += tl.notes
    if not tl.pieces:
        raise RuntimeError("Timeline kosong setelah dipotong")
    zones = _output_zones(ctx, tl, plan["framing"])
    end_t, total = tl.duration, tl.total

    # --- waktu klip asli → waktu hasil
    texts = []
    for t in plan["text_overlays"]:
        start = 0.0 if t["start"] <= 0.5 else tl.map_time(t["start"])
        if start >= end_t:
            continue
        texts.append({**t, "start": round(start, 3), "duration": round(min(t["duration"], end_t - start), 3)})
    ec = plan["end_card"]
    if ec["enabled"]:
        texts.append({"text": ec["text"], "role": "end_card", "start": end_t, "duration": tl.end_card_s,
                      "position": "center", "color": "#FFFFFF", "font": "Anton", "weight": "400"})

    effects = []
    for e in plan["effects"]:
        a = tl.map_time(e["start"])
        effects.append({"type": e["type"], "start": round(a, 3),
                        "end": round(min(end_t, a + (e["end"] - e["start"])), 3)})

    sfx_items = sorted(({**s, "t": tl.map_time(s["t"])} for s in plan["sfx"]), key=lambda s: s["t"])
    teaser_end = max((p.out_e for p in tl.pieces if p.teaser), default=0.0)
    if teaser_end:
        trans = next((x["id"] for x in list_sfx() if x["tag"] in ("swipe", "whoosh")), None)
        if trans:
            sfx_items.insert(0, {"sfx_id": trans, "t": max(0.0, teaser_end - 0.15), "volume": 0.6,
                                 "word": -1, "why": "transisi teaser → isi"})
    bu_sfx, build_ups = build_up_sfx(plan, tl)
    sfx_items += bu_sfx
    auto_sfx, transitions = transition_whooshes(ctx, tl, plan, sfx_items, limits or {})
    sfx_items += auto_sfx
    kept_sfx = []
    gap = (limits or {}).get("min_sfx_gap_s", 1.5)
    for s in sorted(sfx_items, key=lambda s: s["t"]):
        if kept_sfx and not s.get("auto") and not s.get("group") and s["t"] - kept_sfx[-1]["t"] < gap:
            fixes.append(f"sfx {s['sfx_id']}: jadi terlalu dekat setelah jeda dipotong → dibuang")
            continue
        kept_sfx.append(s)

    # 8A-4: isi celah panjang (setelah semua SFX/zoom rencana diketahui)
    fill_z, fill_s, gap_fills = fill_gaps(ctx, tl, plan, list(plan["zooms"]), kept_sfx, texts, effects, limits or {})
    kept_sfx = sorted(kept_sfx + fill_s, key=lambda x: x["t"])
    plan = {**plan, "zooms": list(plan["zooms"]) + fill_z}

    # --- graf video
    graph, vlab, alab = _framing_graph(ctx, tl, plan["framing"])
    parts = [graph]
    chain: list[str] = []
    from backend.editing import visuals
    lim = limits or {}
    t_zooms, t_flashes, visual_transitions = visuals.transition_plan(
        transitions, plan.get("transition_visual", "none"), float(lim.get("transition_zoom", 1.06)))
    zoom = _zoom_filter(ctx, tl, plan, zones, fixes, extra=t_zooms)
    if zoom:
        chain.append(zoom)
    if tl.end_card_s:
        chain.append(f"tpad=stop_mode=clone:stop_duration={tl.end_card_s:.3f}")
    gf = grade_filter(plan["grade"])
    if gf:
        chain.append(gf)
    chain.extend(_build_expression_vf(effects))
    ff = visuals.flash_filter(t_flashes)
    if ff:
        chain.append(ff)
    if tl.end_card_s:
        chain.append(f"drawbox=x=0:y=0:w=iw:h=ih:color=black@0.6:t=fill:enable='gte(t,{end_t:.3f})'")
    parts.append(f"{vlab}{','.join(chain) or 'null'}[vpre]")

    labels = visuals.name_labels(ctx, tl, plan) if plan.get("name_labels") else []
    accent = ((lim.get("palette") or {}).get("accent")) or "#FF3B6B"
    ass, sub_bands = _build_ass(ctx, tl, plan, zones, texts, visuals.label_events(labels, accent))
    emojis = visuals.emoji_items(plan, tl, zones, sub_bands)
    post_ass: list[str] = []
    if ass:
        ass_path = os.path.join(work_dir, "subs.ass")
        with open(ass_path, "w", encoding="utf-8") as f:
            f.write(ass)
        post_ass.append(f"ass=filename={ass_path}:fontsdir={FONT_DIR}")

    # --- graf audio
    inputs = ["-i", ctx["clip_path"]]
    pad = f",apad=pad_dur={tl.end_card_s:.3f}" if tl.end_card_s else ""
    parts.append(f"{alab}aformat=sample_rates=44100:channel_layouts=stereo{pad}[voice0]")
    mix = []
    music_info = None
    m = plan["music"]
    track = pick_track(m["mood"], m["energy"], seed) if m["enabled"] else None
    if m["enabled"] and not track:
        fixes.append("music: tidak ada lagu berlisensi yang cocok → tanpa musik")
    music_moments: list[dict] = []
    if track:
        from backend.editing.music import music_automation
        music_vol, music_moments = music_automation(plan, tl, m["volume_db"])
        inputs += ["-stream_loop", "-1", "-i", track["path"]]
        mi = inputs.count("-i") - 1
        fade_out = max(0.0, total - 1.5)
        parts.append("[voice0]asplit=2[voice][sc]")
        parts.append(f"[{mi}:a]aformat=sample_rates=44100:channel_layouts=stereo,"
                     f"atrim=0:{total:.3f},asetpts=PTS-STARTPTS,afade=t=in:d=0.8,"
                     f"afade=t=out:st={fade_out:.3f}:d=1.5,{music_vol}[mus]")
        # Musik otomatis mengecil saat ada suara orang (sidechain dari suara)
        parts.append("[mus][sc]sidechaincompress=threshold=0.015:ratio=12:attack=10:release=400[musd]")
        mix += ["[voice]", "[musd]"]
        music_info = {"file": track["file"], "title": track.get("title"), "credit": credit(track)}
    else:
        mix.append("[voice0]")

    for k, s in enumerate(kept_sfx):
        path = _sfx_path(s["sfx_id"])
        if not path:
            fixes.append(f"sfx {s['sfx_id']}: file hilang → dilewati")
            continue
        inputs += ["-i", path]
        idx = inputs.count("-i") - 1
        ms = int(s["t"] * 1000)
        # 8B-3: level disamakan (+ limiter untuk transien), lalu volume pilihan rencana
        parts.append(f"[{idx}:a]aformat=sample_rates=44100:channel_layouts=stereo,"
                     f"{norm_filter(s['sfx_id'], s['volume'])},adelay={ms}|{ms}[sfx{k}]")
        mix.append(f"[sfx{k}]")
    parts.append(f"{''.join(mix)}amix=inputs={len(mix)}:normalize=0:duration=first,"
                 f"{_LOUDNESS},aresample=44100[afinal]")

    first_emoji = inputs.count("-i")
    for e in emojis:
        inputs += ["-loop", "1", "-framerate", f"{ctx['fps']:g}", "-t", f"{total:.3f}", "-i", e["png"]]
    em_parts, vcur = visuals.emoji_overlay_graph(emojis, first_emoji, "[vpre]", ctx["fps"])
    parts += em_parts
    if post_ass:
        parts.append(f"{vcur}{','.join(post_ass)}[vass]")
        vcur = "[vass]"
    if plan.get("progress_bar"):
        pb_parts, vcur = visuals.progress_graph(vcur, total, lim.get("progress_color", "#FFFFFF"), ctx["fps"])
        parts += pb_parts
    parts.append(f"{vcur}format=yuv420p[vfinal]")

    script = os.path.join(work_dir, "render_graph.txt")
    with open(script, "w", encoding="utf-8") as f:
        f.write(";".join(parts))

    cmd = [
        "ffmpeg", "-loglevel", "error", "-y", *inputs,
        "-filter_complex_script", script,
        "-map", "[vfinal]", "-map", "[afinal]",
        "-c:v", "libx264", "-preset", "veryfast" if fast else "medium", "-crf", "26" if fast else "20",
        "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", "-t", f"{total:.3f}",
        output_path,
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"FFmpeg gagal: {res.stderr[-800:]}")
    return {
        "fixes": fixes,
        "duration": round(total, 2),
        "source_duration": ctx["duration"],
        "cut_seconds": tl.cut_seconds,
        "teaser_s": round(teaser_end, 2),
        "music": music_info,
        "rhythm": measure_rhythm(tl, ctx, plan["zooms"], kept_sfx, texts, effects),
        "transitions": transitions,
        "build_ups": build_ups,
        "gap_fills": gap_fills,
        "visual_transitions": visual_transitions,
        "music_moments": music_moments,
        "emojis": [{k: e[k] for k in ("emoji", "t", "cx", "cy")} for e in emojis],
        "name_labels": [{k: lb[k] for k in ("name", "role", "t")} for lb in labels],
    }


def render_preview(ctx: dict, recipe: dict, output_path: str, work_dir: str, seconds: float = 4.5) -> dict:
    """Potongan pendek klip asli dengan resep subtitle — mesin yang sama dengan Auto Edit."""
    from backend.editing.timeline import Piece

    words = ctx["words"]
    sen = next((x for x in ctx["sentences"] if x["to_word"] - x["from_word"] >= 5 and x["s"] >= 0.5),
               ctx["sentences"][0] if ctx["sentences"] else None)
    t0 = max(0.0, (sen["s"] if sen else 0.0) - 0.15)
    t1 = min(ctx["duration"], t0 + seconds)
    fps = ctx["fps"]
    pieces, out_t = [], 0.0
    for k, sh in enumerate(ctx["shots"]):
        a, b = max(t0, sh["t_start"]), min(t1, sh["t_end"])
        fa, fb = round(a * fps), round(b * fps)
        if fb - fa >= 4:
            pieces.append(Piece(fa / fps, fb / fps, out_t, out_t + (fb - fa) / fps, k))
            out_t += (fb - fa) / fps
    tl = Timeline(pieces=pieces, duration=out_t)
    in_win = [w for w in words if t0 <= w["s"] < t1]
    hl = [max(in_win, key=lambda w: len(w["w"].strip(".,!?")))["i"]] if in_win else []
    plan = {
        "framing": "grid_auto", "subtitle": {"enabled": True, "recipe": recipe, "highlight_words": hl},
        "grade": {"contrast": 1.0, "saturation": 1.0, "brightness": 0.0, "gamma": 1.0, "warmth": 0.0, "tint": 0.0},
        "text_overlays": [], "effects": [], "sfx": [], "zooms": [],
        "music": {"enabled": False, "mood": "chill", "energy": "mid", "volume_db": -20},
        "end_card": {"enabled": False, "text": "", "duration_s": 0},
        "cuts": {}, "hook_teaser": {"enabled": False},
    }
    return render_plan(ctx, plan, output_path, work_dir, timeline=tl, fast=True)
