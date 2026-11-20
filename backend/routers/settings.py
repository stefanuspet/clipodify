"""Router: settings — API keys dan tmp file management."""

from __future__ import annotations

import logging
import os

from fastapi import APIRouter, HTTPException

from backend.schemas import AddApiKeyRequest, ApiKeyResponse, TmpUsageResponse

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/settings", tags=["settings"])


# ---------------------------------------------------------------- helpers

def _mask(value: str) -> str:
    if len(value) <= 12:
        return "•" * len(value)
    return f"{value[:6]}{'•' * 8}{value[-4:]}"


def _key_to_response(k: dict) -> ApiKeyResponse:
    return ApiKeyResponse(
        id=k["id"],
        name=k["name"],
        key_masked=_mask(k["key_value"]),
        is_active=bool(k["is_active"]),
    )


# ---------------------------------------------------------------- api keys

@router.get("/keys", response_model=list[ApiKeyResponse])
def list_keys():
    from db.database import get_api_keys
    return [_key_to_response(k) for k in get_api_keys()]


@router.post("/keys", response_model=ApiKeyResponse)
def add_key(body: AddApiKeyRequest):
    from db.database import add_api_key, get_api_keys
    try:
        key_id = add_api_key(body.name, body.key_value, make_active=body.make_active)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    keys = get_api_keys()
    key = next((k for k in keys if k["id"] == key_id), None)
    if not key:
        raise HTTPException(status_code=500, detail="Key tersimpan tapi tidak bisa dibaca")
    return _key_to_response(key)


@router.post("/keys/{key_id}/activate", response_model=dict)
def activate_key(key_id: int):
    from db.database import get_api_keys, set_active_api_key
    keys = get_api_keys()
    if not any(k["id"] == key_id for k in keys):
        raise HTTPException(status_code=404, detail="Key tidak ditemukan")
    set_active_api_key(key_id)
    return {"ok": True}


@router.delete("/keys/{key_id}", response_model=dict)
def delete_key(key_id: int):
    from db.database import delete_api_key, get_api_keys
    keys = get_api_keys()
    if not any(k["id"] == key_id for k in keys):
        raise HTTPException(status_code=404, detail="Key tidak ditemukan")
    delete_api_key(key_id)
    return {"ok": True}


@router.post("/keys/{key_id}/test", response_model=dict)
def test_key(key_id: int):
    from db.database import get_api_keys
    from core.gemini_analyzer import test_api_key
    keys = get_api_keys()
    key = next((k for k in keys if k["id"] == key_id), None)
    if not key:
        raise HTTPException(status_code=404, detail="Key tidak ditemukan")
    ok, message = test_api_key(key["key_value"])
    return {"ok": ok, "message": message}


# ---------------------------------------------------------------- tmp files

@router.get("/tmp", response_model=TmpUsageResponse)
def tmp_usage():
    from utils.constants import TMP_DIR
    total = 0
    count = 0
    if os.path.isdir(TMP_DIR):
        for name in os.listdir(TMP_DIR):
            path = os.path.join(TMP_DIR, name)
            if os.path.isfile(path):
                total += os.path.getsize(path)
                count += 1
    return TmpUsageResponse(count=count, total_bytes=total, total_mb=round(total / 1_048_576, 1))


@router.delete("/tmp", response_model=dict)
def clear_tmp():
    from utils.constants import TMP_DIR
    removed = 0
    errors = 0
    if os.path.isdir(TMP_DIR):
        for name in os.listdir(TMP_DIR):
            path = os.path.join(TMP_DIR, name)
            if os.path.isfile(path):
                try:
                    os.remove(path)
                    removed += 1
                except OSError:
                    errors += 1
    return {"removed": removed, "errors": errors}


# ---------------------------------------------------------------- integrasi (Google Fonts, dll.)

INTEGRATION_KEYS = {"google_fonts_api_key": "GOOGLE_FONTS_API_KEY"}


@router.get("/integrations")
def get_integrations():
    from db.database import get_setting
    out = {}
    for key, env in INTEGRATION_KEYS.items():
        value = get_setting(key) or os.getenv(env, "")
        out[key] = {"set": bool(value), "masked": _mask(value) if value else ""}
    return out


@router.put("/integrations")
def put_integrations(body: dict):
    from db.database import set_setting
    for key in INTEGRATION_KEYS:
        if key in body:
            set_setting(key, (body[key] or "").strip() or None)
    if "google_fonts_api_key" in body:
        from backend.editing.fonts import clear_catalog_cache
        clear_catalog_cache()
    return get_integrations()


@router.post("/integrations/google-fonts/test")
def test_google_fonts():
    from backend.editing.fonts import fetch_catalog
    try:
        fonts = fetch_catalog(force=True)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True, "families": len(fonts)}
