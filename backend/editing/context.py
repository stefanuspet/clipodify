"""Context pack: semua yang perlu Gemini ketahui tentang satu klip, dihitung dari file klip itu.

Timestamp per kata dan sinyal emosi tidak disimpan di DB saat job jalan, jadi dihitung
ulang di sini dari klipnya (pendek, jadi cepat), lalu di-cache per kandidat.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from dataclasses import asdict

import cv2

logger = logging.getLogger(__name__)

CONTEXT_VERSION = 2

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
CACHE_ROOT = os.path.join(_ROOT, "tmp", "edit")


def cache_dir(candidate_id: int) -> str:
    d = os.path.join(CACHE_ROOT, str(candidate_id))
    os.makedirs(d, exist_ok=True)
    return d


def _load_candidate(candidate_id: int) -> dict:
    from db.database import _connect
    with _connect() as conn:
        row = conn.execute(
            """SELECT c.*, j.title AS video_title, j.mode AS job_mode,
                      j.campaign_product AS campaign_product, j.url AS job_url
               FROM candidates c JOIN jobs j ON c.job_id = j.id WHERE c.id = ?""",
            (candidate_id,),
        ).fetchone()
    if not row:
        raise ValueError(f"Kandidat {candidate_id} tidak ditemukan")
    cand = dict(row)
    for f in ("parts", "people", "hashtags"):
        v = cand.get(f)
        if isinstance(v, str):
            try:
                cand[f] = json.loads(v)
            except json.JSONDecodeError:
                cand[f] = None
    return cand


def _ensure_clip(cand: dict, out_dir: str) -> str:
    path = cand.get("output_path")
    if path and os.path.exists(path):
        return path
    from core import source
    src = cand.get("job_url")
    clip = os.path.join(out_dir, "clip.mp4")
    if not os.path.exists(clip):
        source.extract_clip(src, cand.get("parts") or [], clip)
    return clip


def _extract_wav(clip: str, out_dir: str) -> str:
    wav = os.path.join(out_dir, "audio.wav")
    subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-y", "-i", clip, "-ac", "1", "-ar", "16000", wav],
        check=True,
    )
    return wav


def _words_and_sentences(wav: str) -> tuple[list[dict], list[dict]]:
    from core.transcriber import transcribe
    tr = transcribe(wav)
    words: list[dict] = []
    sentences: list[dict] = []
    for seg in tr["segments"]:
        first = len(words)
        for w in seg.get("words") or []:
            text = (w.get("word") or "").strip()
            if not text:
                continue
            words.append({"i": len(words), "w": text,
                          "s": round(float(w["start"]), 3), "e": round(float(w["end"]), 3)})
        if len(words) > first:
            sentences.append({
                "s": round(float(seg["start"]), 3), "e": round(float(seg["end"]), 3),
                "text": seg["text"].strip(), "from_word": first, "to_word": len(words) - 1,
            })
    return words, sentences


def _audio_emotions(wav: str) -> list[dict]:
    try:
        from backend.analyzer.audio import analyze_audio
        res = analyze_audio(wav)
    except Exception as e:
        logger.warning("Emosi audio dilewati: %s", e)
        return []
    return [
        {"s": round(float(s["start"]), 2), "e": round(float(s["end"]), 2),
         "emotion": s.get("emotion"), "intensity": round(float(s.get("intensity") or 0), 2)}
        for s in res.get("segments", [])
    ]


def _shots(clip: str, duration: float) -> list[dict]:
    """Shot + layout grid + wajah, tanpa person tracker (tidak dibutuhkan lagi oleh planner)."""
    from backend.analyzer.grid_composer import plan_grid_segments
    segs = plan_grid_segments([], {}, duration, video_path=clip)
    out = []
    for sg in segs:
        out.append({
            "t_start": sg.t_start, "t_end": sg.t_end, "layout": sg.layout,
            "persons": sg.persons,
            "bboxes": {k: asdict(v) for k, v in sg.bboxes.items()},
            "face_bboxes": {k: asdict(v) for k, v in (sg.face_bboxes or {}).items()},
            "x_bounds": {k: list(v) for k, v in (sg.x_bounds or {}).items()},
            "speak": dict(sg.speak_scores or {}),
        })
    return out


_ID_SIM = 0.38          # cosine minimal "orang yang sama" (uji klip 258: sama ≥0.41, beda ≤0.30)
_SPEAK_MIN = 0.008      # skor gerak bibir minimal untuk dianggap pembicara di shot multi-orang


def _identities(clip: str, shots: list[dict], words: list[dict], sentences: list[dict],
                fps: float) -> list[dict]:
    """Kelompokkan wajah lintas shot jadi orang (ID1, ID2, …).

    Mengisi shots[k]["identity"] = {person_id_lokal: "IDn"} dan mengembalikan ringkasan
    per orang (lama tampil, lama bicara, contoh kalimat) untuk Gemini dan thumbnail.
    """
    import numpy as np
    from backend.analyzer.face_detector import FaceBox, face_embedding

    cap = cv2.VideoCapture(clip)
    items = []
    for k, sh in enumerate(shots):
        sh["identity"] = {}
        if not sh["face_bboxes"]:
            continue
        dur = sh["t_end"] - sh["t_start"]
        embs: dict[str, list] = {}
        for q in ((0.3, 0.7) if dur >= 1.5 else (0.5,)):
            cap.set(cv2.CAP_PROP_POS_FRAMES, int((sh["t_start"] + dur * q) * fps))
            ok, frame = cap.read()
            if not ok:
                continue
            for pid, fb in sh["face_bboxes"].items():
                e = face_embedding(frame, FaceBox(**fb))
                if e is not None:
                    embs.setdefault(pid, []).append(e)
        for pid, es in embs.items():
            m = np.mean(es, axis=0)
            items.append((k, pid, m / (np.linalg.norm(m) + 1e-9), sh["face_bboxes"][pid]["h"]))
    cap.release()

    # Wajah besar dulu (embedding paling andal) supaya pusat kelompok bagus
    items.sort(key=lambda x: -x[3])
    clusters: list[dict] = []
    for k, pid, e, fh in items:
        best, best_sim = None, _ID_SIM
        for c in clusters:
            if k in c["shots"]:          # dua wajah di shot yang sama pasti orang berbeda
                continue
            sim = float((c["sum"] / np.linalg.norm(c["sum"])) @ e)
            if sim > best_sim:
                best, best_sim = c, sim
        if best is None:
            best = {"sum": np.zeros_like(e), "members": [], "shots": set(), "face_h": 0}
            clusters.append(best)
        best["sum"] += e
        best["members"].append((k, pid))
        best["shots"].add(k)
        best["face_h"] = max(best["face_h"], fh)

    def words_in(sh):
        return [w for w in words if sh["t_start"] <= (w["s"] + w["e"]) / 2 < sh["t_end"]]

    def is_speaker(sh, pid) -> bool:
        real = [p for p in sh["persons"] if p != "_none"]
        if len(real) == 1:
            return True
        sc = sh.get("speak") or {}
        if not sc or pid not in sc:
            return False
        ranked = sorted(sc.values(), reverse=True)
        return sc[pid] == ranked[0] and sc[pid] >= _SPEAK_MIN and \
            (len(ranked) == 1 or sc[pid] >= ranked[1] * 1.5)

    people = []
    for c in clusters:
        screen = speak = 0.0
        lines: list[str] = []
        for k, pid in c["members"]:
            sh = shots[k]
            screen += sh["t_end"] - sh["t_start"]
            if is_speaker(sh, pid):
                ws = words_in(sh)
                speak += sum(w["e"] - w["s"] for w in ws)
                for sen in sentences:
                    if sh["t_start"] <= sen["s"] < sh["t_end"] and len(lines) < 3:
                        lines.append(sen["text"][:120])
        people.append({"screen_s": round(screen, 1), "speak_s": round(speak, 1),
                       "shots": sorted(c["shots"]), "lines": lines, "members": c["members"],
                       "face_h": c["face_h"]})

    people.sort(key=lambda p: (-p["speak_s"], -p["screen_s"]))
    for n, p in enumerate(people, 1):
        p["id"] = f"ID{n}"
        for k, pid in p.pop("members"):
            shots[k]["identity"][pid] = p["id"]
    return people


def shots_to_segments(shots: list[dict]):
    """Balikkan shot di context ke GridSegment untuk renderer."""
    from backend.analyzer.grid_composer import GridSegment
    from backend.analyzer.person_tracker import BBox
    from backend.analyzer.face_detector import FaceBox
    return [
        GridSegment(
            t_start=s["t_start"], t_end=s["t_end"], layout=s["layout"], persons=list(s["persons"]),
            bboxes={k: BBox(**v) for k, v in s["bboxes"].items()},
            face_bboxes={k: FaceBox(**v) for k, v in s["face_bboxes"].items()} or None,
            x_bounds={k: tuple(v) for k, v in s["x_bounds"].items()} or None,
        )
        for s in shots
    ]


def build_context(candidate_id: int, progress=None) -> dict:
    """Bangun (atau ambil dari cache) context pack untuk satu kandidat."""
    def _p(label: str) -> None:
        if progress:
            progress(label)

    out_dir = cache_dir(candidate_id)
    ctx_path = os.path.join(out_dir, "context.json")
    if os.path.exists(ctx_path):
        with open(ctx_path, encoding="utf-8") as f:
            ctx = json.load(f)
        if ctx.get("version") == CONTEXT_VERSION and os.path.exists(ctx.get("clip_path", "")):
            if "colors" not in ctx:          # konteks lama: tambahkan analisis warna saja
                _p("Analisis warna video...")
                from backend.editing.color_analysis import analyze_colors
                ctx["colors"] = analyze_colors(ctx)
                with open(ctx_path, "w", encoding="utf-8") as f:
                    json.dump(ctx, f, ensure_ascii=False)
            return ctx

    cand = _load_candidate(candidate_id)
    _p("Menyiapkan file klip...")
    clip = _ensure_clip(cand, out_dir)

    cap = cv2.VideoCapture(clip)
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    duration = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) / fps
    cap.release()

    _p("Transkrip per kata (Whisper)...")
    wav = _extract_wav(clip, out_dir)
    words, sentences = _words_and_sentences(wav)

    _p("Analisis emosi audio...")
    emotions = _audio_emotions(wav)

    _p("Deteksi shot, orang, dan pembicara...")
    shots = _shots(clip, duration)

    _p("Mengenali orang lintas shot...")
    people = _identities(clip, shots, words, sentences, fps)

    ctx = {
        "version": CONTEXT_VERSION,
        "candidate_id": candidate_id,
        "clip_path": clip,
        "fps": fps, "width": width, "height": height, "duration": round(duration, 3),
        "meta": {
            "video_title": cand.get("video_title"),
            "title": cand.get("title"),
            "hook": cand.get("hook"),
            "clip_type": cand.get("clip_type"),
            "reason": cand.get("reason"),
            "description": cand.get("description"),
            "people": cand.get("people") or [],
            "mode": cand.get("job_mode"),
            "campaign_product": cand.get("campaign_product"),
        },
        "words": words,
        "sentences": sentences,
        "audio_emotions": emotions,
        "shots": shots,
        "people": people,
    }
    _p("Analisis warna video...")
    from backend.editing.color_analysis import analyze_colors
    ctx["colors"] = analyze_colors(ctx)
    with open(ctx_path, "w", encoding="utf-8") as f:
        json.dump(ctx, f, ensure_ascii=False)
    return ctx
