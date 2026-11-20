"""Router: Studio Subtitle — rancang resep gaya subtitle (aset), preview, dan generate via Gemini."""

from __future__ import annotations

import glob
import json
import logging
import os

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/studio", tags=["studio"])

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
PREVIEW_DIR = os.path.join(_ROOT, "tmp", "studio")


class StyleIn(BaseModel):
    id: str | None = None
    recipe: dict
    favorite: bool | None = None
    notes: str | None = None


class PreviewIn(BaseModel):
    recipe: dict
    candidate_id: int | None = None


class GenerateIn(BaseModel):
    description: str
    base_style_id: str | None = None


@router.get("/styles")
def styles():
    from backend.editing.subtitle_style import list_styles
    return list_styles()


@router.post("/styles")
def save(body: StyleIn):
    from backend.editing.subtitle_style import save_style
    return save_style(body.recipe, created_by="user", style_id=body.id, favorite=body.favorite, notes=body.notes)


@router.delete("/styles/{style_id}")
def delete(style_id: str):
    from backend.editing.subtitle_style import delete_style
    if not delete_style(style_id):
        raise HTTPException(status_code=404, detail="Gaya tidak ditemukan.")
    return {"deleted": style_id}


@router.get("/options")
def options():
    """Pilihan untuk form Studio: font Google, enum resep, resep default, status API key."""
    from backend.editing.fonts import api_key, curated
    from backend.editing import subtitle_style as ss
    return {
        "fonts": curated(80),
        "google_fonts_key": bool(api_key()),
        "anim_types": ss.ANIM_TYPES, "active_modes": ss.ACTIVE_MODES, "color_modes": ss.COLOR_MODES,
        "cases": ss.CASES, "positions": ss.POSITIONS, "weights": ss.WEIGHTS,
        "default_recipe": ss.DEFAULT_RECIPE,
    }


@router.get("/clips")
def clips():
    """Klip yang konteksnya sudah ada (preview instan). Klip lain tetap bisa, tapi pertama kali lambat."""
    from backend.editing.context import CACHE_ROOT
    out = []
    for path in glob.glob(os.path.join(CACHE_ROOT, "*", "context.json")):
        try:
            with open(path, encoding="utf-8") as f:
                ctx = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        out.append({"candidate_id": ctx["candidate_id"], "title": ctx["meta"].get("title"),
                    "duration": ctx["duration"]})
    return sorted(out, key=lambda c: -c["candidate_id"])


@router.post("/preview")
def preview(body: PreviewIn):
    from backend.editing.context import build_context, cache_dir
    from backend.editing.renderer import render_preview
    from backend.editing.subtitle_style import recipe_hash, validate_recipe

    cands = clips()
    cid = body.candidate_id or (cands[0]["candidate_id"] if cands else None)
    if not cid:
        raise HTTPException(status_code=400, detail="Belum ada klip untuk preview. Jalankan Auto Edit sekali.")
    fixes: list[str] = []
    recipe = validate_recipe(body.recipe, fixes)
    os.makedirs(PREVIEW_DIR, exist_ok=True)
    name = f"preview_{cid}_{recipe_hash(recipe)}.mp4"
    out = os.path.join(PREVIEW_DIR, name)
    if not os.path.exists(out):
        try:
            ctx = build_context(cid)
            render_preview(ctx, recipe, out, cache_dir(cid))
        except Exception as exc:
            logger.exception("Preview gagal")
            raise HTTPException(status_code=500, detail=str(exc))
    from backend.editing.color_analysis import check_subtitle_contrast
    warnings: list[str] = []
    try:
        _, warnings = check_subtitle_contrast(build_context(cid).get("colors") or [], recipe)
    except Exception:
        pass
    return {"url": f"/api/studio/preview/{name}", "candidate_id": cid, "fixes": fixes + warnings, "recipe": recipe}


@router.get("/preview/{name}")
def preview_file(name: str):
    path = os.path.join(PREVIEW_DIR, os.path.basename(name))
    if not name.endswith(".mp4") or not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Preview tidak ditemukan.")
    return FileResponse(path, media_type="video/mp4")


@router.post("/generate")
def generate(body: GenerateIn):
    """Gemini mengisi resep dari deskripsi bebas (1 panggilan). Hasil TIDAK otomatis disimpan."""
    from google.genai import types
    import core.gemini_client as gemini_client
    from backend.editing.plan_schema import catalog, recipe_schema
    from backend.editing.subtitle_style import get_style, validate_recipe

    if not body.description.strip():
        raise HTTPException(status_code=400, detail="Deskripsi kosong.")
    cat = catalog()
    base = get_style(body.base_style_id) if body.base_style_id else None
    prompt = (
        "Rancang resep gaya subtitle video pendek (kanvas 1080×1920) sesuai deskripsi user.\n"
        f"Deskripsi: {body.description.strip()}\n"
        + (f"Mulai dari resep ini lalu ubah sesuai deskripsi: {json.dumps(base['recipe'], ensure_ascii=False)}\n"
           if base else "")
        + "Panduan: size 110–150 untuk caption besar; outline.width relatif ukuran font (0.2 = sangat tebal); "
          "words_per_line 1 = satu kata per layar; color_mode cycle_word = warna berganti tiap kata; "
          "animasi pop/bounce energik, fade/slide_up tenang, wipe dramatis. Wajib terbaca di atas video. "
          "Warna hex #RRGGBB. Beri `name` singkat dalam Bahasa Indonesia."
    )
    config = types.GenerateContentConfig(response_mime_type="application/json",
                                         response_schema=recipe_schema(cat), temperature=0.8)
    try:
        response, model = gemini_client.generate(prompt, config)
        raw = json.loads(response.text)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Gemini gagal: {exc}")
    fixes: list[str] = []
    return {"recipe": validate_recipe(raw, fixes), "fixes": fixes, "model": model}
