"""Router: video editor — render clip dengan subtitle, filter, SFX, text overlay."""

from __future__ import annotations

import logging
import os
import uuid

from fastapi import APIRouter, BackgroundTasks, HTTPException, UploadFile
from fastapi.responses import FileResponse

from backend.schemas import RenderRequest

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/editor", tags=["editor"])

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
_SFX_DIR      = os.path.join(_ROOT, "assets", "sfx")


@router.get("/expressions")
def list_expressions():
    from backend.renderer import get_expressions
    return get_expressions()


@router.get("/sfx")
def list_sfx():
    from backend.renderer import list_sfx as _list_sfx
    return _list_sfx()


@router.post("/sfx/upload")
async def upload_sfx(file: UploadFile):
    """Unggah ke KOTAK MASUK — belum masuk library sampai lisensinya diisi lewat /assets/import."""
    from backend.editing.audio_library import save_upload
    try:
        name = save_upload(file.filename or "", await file.read())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"inbox": name}


@router.delete("/sfx/{sfx_id}")
def delete_sfx(sfx_id: str):
    """Pindahkan ke assets/sfx/.trash (bisa dikembalikan), bukan hapus permanen."""
    from backend.editing.audio_library import trash_sfx
    from backend.renderer import list_sfx as _list_sfx
    for sfx in _list_sfx():
        if sfx["id"] == sfx_id:
            trash_sfx(sfx["path"])
            return {"ok": True}
    raise HTTPException(status_code=404, detail="SFX tidak ditemukan.")


from pydantic import BaseModel as _BM0


class ImportAudioRequest(_BM0):
    name: str
    kind: str                 # "sfx" | "music"
    info: dict                # sfx: tag, name, use, intensity, align | musik: title, mood[], energy
    license: dict             # kind, source, url, type, attribution_required, attribution_text, notes


@router.get("/assets/inbox")
def assets_inbox():
    from backend.editing.audio_library import LICENSES, list_inbox
    from backend.editing.music import MOODS
    from backend.renderer import list_sfx as _list_sfx
    return {"files": list_inbox(), "licenses": LICENSES, "moods": MOODS,
            "sfx_tags": sorted({x["tag"] for x in _list_sfx()})}


@router.get("/assets/inbox/{name}")
def assets_inbox_file(name: str):
    from backend.editing.audio_library import INBOX
    path = os.path.join(INBOX, os.path.basename(name))
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="File tidak ditemukan.")
    return FileResponse(path)


@router.post("/assets/import")
def assets_import(body: ImportAudioRequest):
    from backend.editing.audio_library import import_file
    try:
        return import_file(body.name, body.kind, body.info, body.license)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


# ---------------------------------------------------------------- segments (subtitle source)

@router.get("/segments/{job_id}")
def get_segments(job_id: int):
    from db.database import get_segments_by_job
    segments = get_segments_by_job(job_id)
    return {"segments": segments, "count": len(segments)}


# ---------------------------------------------------------------- render

_active_renders: dict[str, dict] = {}


@router.post("/render")
def start_render(body: RenderRequest, bg: BackgroundTasks):
    """Render manual: pilihan dari Editor dirakit jadi plan lalu dirender mesin yang sama dengan Auto Edit."""
    from db.database import get_candidates_by_job
    if not any(c["id"] == body.candidate_id for c in get_candidates_by_job(body.job_id)):
        raise HTTPException(status_code=404, detail="Candidate tidak ditemukan.")
    render_id = uuid.uuid4().hex
    _active_renders[render_id] = {"status": "pending", "progress": 0, "total": 2, "kind": "manual",
                                  "candidate_id": body.candidate_id}
    bg.add_task(_run_manual_render, render_id, body.candidate_id, body.config.model_dump())
    return {"render_id": render_id, "status": "pending"}


def _manual_plan(cfg: dict) -> dict:
    from backend.editing.subtitle_style import get_style, validate_recipe
    style = get_style(cfg.get("subtitle_style_id") or "") if cfg.get("subtitle_style_id") else None
    g = cfg.get("grade") or {}

    def clamp(v, lo, hi, d):
        try:
            return max(lo, min(hi, float(v)))
        except (TypeError, ValueError):
            return d
    return {
        "people": [], "beats": [], "intent": "manual", "reasoning": "render manual", "framing": "grid_auto",
        "subtitle": {"enabled": bool(style), "style_id": style["id"] if style else None,
                     "recipe": validate_recipe(style["recipe"]) if style else None, "highlight_words": []},
        "grade": {"contrast": clamp(g.get("contrast"), 0.85, 1.4, 1.0),
                  "saturation": clamp(g.get("saturation"), 0.6, 1.5, 1.0),
                  "brightness": clamp(g.get("brightness"), -0.1, 0.1, 0.0),
                  "gamma": clamp(g.get("gamma"), 0.8, 1.2, 1.0),
                  "warmth": clamp(g.get("warmth"), -1, 1, 0.0), "tint": clamp(g.get("tint"), -1, 1, 0.0)},
        "sfx": [{"sfx_id": s["sfx_id"], "t": max(0.0, float(s["timestamp"])), "volume": clamp(s.get("volume"), 0.1, 1.5, 1.0),
                 "word": -1, "why": "manual"} for s in cfg.get("sfx") or []],
        "text_overlays": [{"text": t["text"], "role": "emphasis", "start": float(t["start"]),
                           "duration": float(t.get("duration") or 3.0),
                           "position": t.get("position") if t.get("position") in ("top", "center", "bottom") else "top",
                           "color": t.get("color") or "#FFFFFF", "font": t.get("font") or "Anton"}
                          for t in cfg.get("text_overlays") or [] if (t.get("text") or "").strip()],
        "effects": [{"type": e["type"], "start": float(e["start"]), "end": float(e["end"])}
                    for e in cfg.get("expressions") or [] if float(e["end"]) > float(e["start"])],
        "cuts": {"remove_silence": False, "remove_fillers": False, "ranges": []},
        "hook_teaser": {"enabled": False, "from_word": 0, "to_word": 0}, "zooms": [],
        "music": {"enabled": False, "mood": "chill", "energy": "mid", "volume_db": -20.0},
        "end_card": {"enabled": False, "text": "", "duration_s": 2.5},
        "transition_sfx": False,          # render manual: tidak menambah SFX otomatis
        "fill_gaps": False, "build_ups": [],
        "emojis": [], "transition_visual": "none", "name_labels": False, "progress_bar": False,
    }


def _run_manual_render(render_id: str, candidate_id: int, cfg: dict) -> None:
    from backend.editing.context import build_context, cache_dir
    from backend.editing.persona import load_persona
    from backend.editing.renderer import render_plan
    from backend.websocket import emit_done, emit_error, emit_progress
    from utils.constants import OUTPUT_DIR

    channel = f"render_{render_id}"
    info = _active_renders[render_id]
    info["status"] = "running"
    try:
        emit_progress(channel, 1, 2, "Menyiapkan konteks klip...")
        ctx = build_context(candidate_id)
        emit_progress(channel, 2, 2, "Render...")
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        out = os.path.join(OUTPUT_DIR, f"edit_{candidate_id}_{render_id[:8]}.mp4")
        rinfo = render_plan(ctx, _manual_plan(cfg), out, cache_dir(candidate_id),
                            load_persona()["limits"], seed=render_id)
        info.update({"status": "done", "output_path": out, "fixes": rinfo["fixes"]})
        emit_done(channel)
    except Exception as exc:
        logger.exception("Render manual %s gagal", render_id)
        info.update({"status": "error", "error": str(exc)})
        emit_error(channel, str(exc))


@router.get("/render/{render_id}/status")
def render_status(render_id: str):
    info = _active_renders.get(render_id)
    if not info:
        raise HTTPException(status_code=404, detail="Render tidak ditemukan.")
    return info


@router.get("/render/{render_id}/file")
def render_file(render_id: str):
    info = _active_renders.get(render_id)
    if not info:
        raise HTTPException(status_code=404, detail="Render tidak ditemukan.")
    if info.get("status") != "done":
        raise HTTPException(status_code=409, detail=f"Render belum selesai: {info.get('status')}.")
    path = info.get("output_path", "")
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="File render tidak ditemukan.")
    return FileResponse(path, media_type="video/mp4", filename=os.path.basename(path))


# ---------------------------------------------------------------- auto-config

from pydantic import BaseModel as _BM
# ---------------------------------------------------------------- auto-plan (Gemini editor)

class AutoPlanRequest(_BM):
    candidate_id: int


@router.post("/auto-plan")
def auto_plan(body: AutoPlanRequest, bg: BackgroundTasks):
    """Gemini menyusun rencana edit lengkap, divalidasi, lalu langsung dirender (tanpa review)."""
    render_id = uuid.uuid4().hex
    _active_renders[render_id] = {"status": "pending", "progress": 0, "total": 4, "kind": "auto_plan",
                                  "candidate_id": body.candidate_id}
    bg.add_task(_run_auto_plan, render_id, body.candidate_id)
    return {"render_id": render_id, "status": "pending"}


def _run_auto_plan(render_id: str, candidate_id: int) -> None:
    import json
    from datetime import datetime
    from backend.editing.context import build_context, cache_dir
    from backend.editing.gemini_plan import generate_plan
    from backend.editing.persona import load_persona
    from backend.editing.plan_schema import catalog
    from backend.editing.renderer import render_plan
    from backend.editing.validator import validate_plan
    from backend.websocket import emit_done, emit_error, emit_progress
    from utils.constants import OUTPUT_DIR

    channel = f"render_{render_id}"
    info = _active_renders[render_id]
    info["status"] = "running"

    def _step(n: int, label: str) -> None:
        info["progress"] = n
        info["label"] = label
        emit_progress(channel, n, 4, label)

    try:
        _step(1, "Menyiapkan konteks klip...")
        ctx = build_context(candidate_id, progress=lambda lbl: _step(1, lbl))

        persona = load_persona()
        _step(2, "Gemini menganalisis momen & menyusun rencana edit...")
        raw, model = generate_plan(ctx, persona)

        _step(3, "Validasi rencana...")
        plan, fixes = validate_plan(raw, ctx, catalog(), persona["limits"])

        # Resep subtitle baru/variasi dari Gemini → simpan ke Studio supaya bisa ditinjau user
        from backend.editing.subtitle_style import get_style, recipe_hash, save_style
        base = get_style(plan["subtitle"]["base_style_id"]) if plan["subtitle"]["base_style_id"] != "baru" else None
        if plan["subtitle"]["enabled"] and (not base or recipe_hash(base["recipe"]) != recipe_hash(plan["subtitle"]["recipe"])):
            saved = save_style(plan["subtitle"]["recipe"], created_by="gemini", favorite=False,
                               notes=f"Dibuat Gemini untuk kandidat {candidate_id}"
                                     + (f", turunan dari '{base['name']}'" if base else ""))
            plan["subtitle"]["style_id"] = saved["id"]
        else:
            plan["subtitle"]["style_id"] = plan["subtitle"]["base_style_id"]

        _step(4, "Render (satu kali encode)...")
        work = cache_dir(candidate_id)
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        output_path = os.path.join(OUTPUT_DIR, f"auto_{candidate_id}_{render_id[:8]}.mp4")
        rinfo = render_plan(ctx, plan, output_path, work, persona["limits"], seed=render_id)
        fixes += rinfo.pop("fixes")

        record = {
            "render_id": render_id, "candidate_id": candidate_id, "model": model,
            "persona": {"id": persona["id"], "version": persona["_version"]},
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "raw_plan": raw, "plan": plan, "fixes": fixes, "output_path": output_path,
            "render": rinfo,
        }
        plan_file = os.path.join(work, f"plan_{render_id[:8]}.json")
        with open(plan_file, "w", encoding="utf-8") as f:
            json.dump(record, f, ensure_ascii=False, indent=2)
        try:
            from backend.editing.learning import record_from_file
            from db.database import save_edit_render
            rec = record_from_file(plan_file)
            rec["candidate_title"] = ctx["meta"].get("title")
            save_edit_render(rec)
        except Exception:
            logger.exception("Gagal mencatat render %s untuk learning", render_id)

        info.update({
            "status": "done", "output_path": output_path, "model": model,
            "intent": plan["intent"], "reasoning": plan["reasoning"], "fixes": fixes,
            "persona": persona.get("name", persona["id"]),
            **rinfo,
            "beats": [{"type": b["type"], "s": b["s"], "e": b["e"], "note": b["note"]}
                      for b in plan["beats"]],
            "people": [{**p, **next((q for q in ctx.get("people") or [] if q["id"] == p["id"]), {})}
                       for p in plan["people"]],
        })
        # Nama orang bisa berubah → thumbnail yang sudah di-cache perlu label baru
        from backend.routers.analyzer import _thumb_jobs
        for key in [k for k in _thumb_jobs if k.endswith(f"_{candidate_id}")]:
            _thumb_jobs.pop(key, None)
        emit_done(channel)
    except Exception as exc:
        logger.exception("Auto-plan %s gagal", render_id)
        info.update({"status": "error", "error": str(exc)})
        emit_error(channel, str(exc))


# ---------------------------------------------------------------- learning (Fase 6)

class LinkRequest(_BM):
    video_id: str | None = None


class ApplyExamplesRequest(_BM):
    render_ids: list[str]


@router.get("/learning")
def learning_overview():
    """Render Auto Edit + saran video YouTube pasangannya + laporan fitur edit vs reaksi."""
    from backend.editing.context import CACHE_ROOT
    from backend.editing.learning import backfill, build_report, suggest_videos
    from backend.editing.persona import load_persona
    from db.database import get_yt_videos, list_edit_renders

    backfill(CACHE_ROOT)
    renders = list_edit_renders()
    videos = get_yt_videos()
    by_id = {v["video_id"]: v for v in videos}
    for r in renders:
        v = by_id.get(r.get("yt_video_id") or "")
        r["video"] = ({k: v.get(k) for k in ("video_id", "title", "views", "likes", "comments", "shares",
                                              "avg_view_percentage", "published_at")} if v else None)
        r["suggestions"] = [] if v else suggest_videos(r, videos)
    persona = load_persona()
    return {
        "renders": renders,
        "report": build_report(renders, videos),
        "persona": {"id": persona["id"], "name": persona.get("name"), "examples": persona.get("examples") or []},
        "yt_last_fetch": max((v.get("fetched_at") or "" for v in videos), default=None),
    }


@router.post("/learning/{render_id}/link")
def learning_link(render_id: str, body: LinkRequest):
    from db.database import link_edit_render
    link_edit_render(render_id, body.video_id or None)
    return {"render_id": render_id, "video_id": body.video_id}


@router.delete("/learning/{render_id}")
def learning_hide(render_id: str):
    """Sembunyikan render dari Learning (mis. render tes). File plan & video tidak dihapus."""
    from db.database import hide_edit_render
    hide_edit_render(render_id, True)
    return {"render_id": render_id, "hidden": True}


@router.post("/learning/apply-examples")
def learning_apply_examples(body: ApplyExamplesRequest):
    """Jadikan plan terpilih contoh (few-shot) untuk persona aktif. Hanya dijalankan atas klik user."""
    from backend.editing.learning import apply_examples
    from backend.editing.persona import DEFAULT_PERSONA, PERSONA_DIR
    from db.database import list_edit_renders

    files = {r["render_id"]: r["plan_file"] for r in list_edit_renders()}
    chosen = [files[r] for r in body.render_ids if files.get(r) and os.path.exists(files[r])]
    if len(chosen) != len(body.render_ids):
        raise HTTPException(status_code=400, detail="Sebagian plan tidak ditemukan.")
    applied = apply_examples(os.path.join(PERSONA_DIR, f"{DEFAULT_PERSONA}.yaml"), chosen, _ROOT)
    return {"examples": applied}


# WebSocket: progress render real-time
from fastapi import WebSocket
from backend.websocket import manager

@router.websocket("/ws/render/{render_id}")
async def render_ws(websocket: WebSocket, render_id: str):
    channel = f"render_{render_id}"
    await manager.connect(channel, websocket)
    try:
        while True:
            await websocket.receive_text()
    except Exception:
        manager.disconnect(channel, websocket)
