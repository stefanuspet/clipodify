"""Router: Instagram & Facebook analytics."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from backend.schemas import (
    DiagnoseTokenRequest,
    FacebookAddonRequest,
    MetaConnectRequest,
    MetaFetchRequest,
    MetaPageSelectRequest,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/meta", tags=["meta"])


# ---------------------------------------------------------------- status

@router.get("/status")
def get_status():
    from db.database import get_meta_auth
    auth = get_meta_auth()
    if not auth:
        return {"connected": False}
    return {
        "connected": True,
        "kind": auth.get("kind"),
        "page_name": auth.get("page_name"),
        "ig_username": auth.get("ig_username"),
        "ig_id": auth.get("ig_id"),
        "page_id": auth.get("page_id"),
        "fb_page_id": auth.get("fb_page_id"),
        "fb_page_name": auth.get("fb_page_name"),
        "connected_at": auth.get("connected_at"),
    }


# ---------------------------------------------------------------- connect

@router.post("/connect")
def connect(body: MetaConnectRequest):
    """Proses token dan return info koneksi atau daftar pages untuk dipilih."""
    from core import meta_analytics
    from db.database import save_meta_auth

    token = body.token.strip()
    app_id = body.app_id.strip()
    app_secret = body.app_secret.strip()

    working = token

    # Coba jalur Instagram Login dulu (token IGAA)
    ig_account = meta_analytics.probe_instagram_login(working)

    if ig_account:
        # Tukar ke long-lived token kalau app_secret tersedia
        if app_secret:
            try:
                exchanged = meta_analytics.exchange_long_lived_ig(working, app_secret)
                if exchanged.get("token"):
                    working = exchanged["token"]
                    days = round((exchanged.get("expires_in") or 0) / 86400)
                    return_extra = {"token_days": days}
            except Exception as exc:
                logger.warning("Exchange IG token gagal: %s", exc)
                return_extra = {"token_warning": "Token dipakai apa adanya (tidak ditukar)."}
        else:
            return_extra = {"token_warning": "App Secret kosong — token berlaku ~1 jam."}

        save_meta_auth(
            working, None, None,
            ig_account["ig_id"], ig_account.get("ig_username"),
            kind=meta_analytics.KIND_IG_LOGIN,
        )
        return {
            "connected": True,
            "kind": meta_analytics.KIND_IG_LOGIN,
            "ig_username": ig_account.get("ig_username"),
            "account_type": ig_account.get("account_type"),
            **return_extra,
        }

    # Jalur Facebook Page
    if app_id and app_secret:
        try:
            exchanged = meta_analytics.exchange_long_lived(working, app_id, app_secret)
            if exchanged.get("token"):
                working = exchanged["token"]
        except Exception as exc:
            logger.warning("Exchange FB token gagal: %s", exc)

    try:
        pages = meta_analytics.list_pages(working)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    if not pages:
        # Diagnosis token untuk pesan error yang lebih berguna
        if working.startswith("EAA"):
            detail = (
                "Token Facebook ini tidak mengelola Page mana pun. "
                "Pastikan permission pages_show_list dicentang saat generate token, "
                "dan Page-mu dipilih saat dialog consent muncul."
            )
        elif working.startswith("IGAA"):
            detail = "Token Instagram Login ini ditolak Meta — kemungkinan sudah kedaluwarsa."
        else:
            detail = "Token ini tidak mengelola Facebook Page mana pun."
        raise HTTPException(status_code=422, detail=detail)

    # Return daftar pages → frontend tampilkan picker
    return {"pages": pages, "working_token": working}


@router.post("/connect/page")
def save_page(body: MetaPageSelectRequest):
    """Simpan pilihan Facebook Page setelah user memilih dari daftar."""
    from core import meta_analytics
    from db.database import save_meta_auth

    save_meta_auth(
        body.working_token,
        body.page_id,
        body.page_name,
        body.ig_id,
        body.ig_username,
        kind=meta_analytics.KIND_PAGE,
    )
    return {
        "connected": True,
        "kind": meta_analytics.KIND_PAGE,
        "page_name": body.page_name,
        "ig_username": body.ig_username,
    }


@router.post("/connect/facebook")
def add_facebook(body: FacebookAddonRequest):
    """Tambahkan Facebook Page di atas koneksi Instagram Login yang sudah ada."""
    from core import meta_analytics
    from db.database import get_meta_auth, set_meta_facebook

    auth = get_meta_auth()
    if not auth or auth.get("kind") != meta_analytics.KIND_IG_LOGIN:
        raise HTTPException(
            status_code=400,
            detail="Hanya bisa ditambahkan di atas koneksi Instagram Login.",
        )

    fb_token = body.token.strip()
    try:
        pages = meta_analytics.list_pages(fb_token)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    if not pages:
        raise HTTPException(
            status_code=422,
            detail="Token ini tidak mengelola Facebook Page mana pun.",
        )

    return {"pages": pages, "fb_token": fb_token}


@router.post("/connect/facebook/save")
def save_facebook_page(body: dict):
    from db.database import set_meta_facebook
    page_token = body.get("page_token") or body.get("fb_token", "")
    page_id = body.get("page_id", "")
    page_name = body.get("page_name", "")
    if not page_id:
        raise HTTPException(status_code=400, detail="page_id wajib diisi.")
    set_meta_facebook(page_token, page_id, page_name)
    return {"ok": True, "fb_page_name": page_name}


@router.delete("/disconnect")
def disconnect():
    from db.database import delete_meta_auth
    delete_meta_auth()
    return {"ok": True}


@router.delete("/facebook")
def disconnect_facebook():
    from db.database import set_meta_facebook
    set_meta_facebook(None, None, None)
    return {"ok": True}


# ---------------------------------------------------------------- data

@router.post("/data")
def fetch_data(body: MetaFetchRequest):
    from core import meta_analytics
    from db.database import get_meta_auth

    auth = get_meta_auth()
    if not auth:
        raise HTTPException(status_code=401, detail="Belum terhubung ke Meta.")

    page = {
        "page_id": auth.get("page_id"),
        "page_name": auth.get("page_name"),
        "ig_id": auth.get("ig_id"),
        "ig_username": auth.get("ig_username"),
        "page_token": None,
        "kind": auth.get("kind") or meta_analytics.KIND_PAGE,
        "fb_token": auth.get("fb_token"),
        "fb_page_id": auth.get("fb_page_id"),
    }

    try:
        snapshot = meta_analytics.fetch_snapshot(
            auth["access_token"], page, limit=body.limit
        )
        return snapshot
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ---------------------------------------------------------------- diagnose

@router.post("/token/diagnose")
def diagnose_token(body: DiagnoseTokenRequest):
    from core import meta_analytics
    try:
        return meta_analytics.diagnose_token(body.token.strip())
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
