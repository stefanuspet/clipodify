"""Clipodify — FastAPI backend.

Jalankan dari root project:
    uvicorn backend.main:app --reload --port 8000
"""

from __future__ import annotations

import logging
import os
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)

# Tambahkan root project ke sys.path agar `core/`, `db/`, `utils/` bisa diimport
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from backend.routers import analyzer, candidates, download, editor, jobs, meta, scout, settings, youtube, studio

app = FastAPI(
    title="Clipodify API",
    version="1.0.0",
    description="Backend untuk Clipodify video clip editor",
)

# CORS — izinkan React dev server dan production build
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",   # Vite dev server
        "http://localhost:4173",   # Vite preview
        "http://localhost:3000",   # fallback
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------- startup

@app.on_event("startup")
async def startup_event() -> None:
    import asyncio
    import os
    from db.database import init_db
    from utils.helpers import ensure_dirs
    from backend.websocket import set_main_loop
    init_db()
    ensure_dirs()
    set_main_loop(asyncio.get_running_loop())
    # Suppress HuggingFace Hub unauthenticated warning — model sudah di-cache lokal
    os.environ.setdefault("HF_HUB_DISABLE_IMPLICIT_TOKEN", "1")
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")


# ---------------------------------------------------------------- routers

app.include_router(jobs.router)
app.include_router(candidates.router)
app.include_router(settings.router)
app.include_router(download.router)
app.include_router(youtube.router)
app.include_router(scout.router)
app.include_router(meta.router)
app.include_router(editor.router)
app.include_router(analyzer.router)
app.include_router(studio.router)
# app.include_router(download.router)
# app.include_router(settings.router)
# app.include_router(youtube.router)
# app.include_router(scout.router)
# app.include_router(meta.router)
# app.include_router(editor.router)


# ---------------------------------------------------------------- static files

def _mount_static() -> None:
    from utils.constants import OUTPUT_DIR, TMP_DIR
    _ASSETS_DIR = os.path.join(_ROOT, "assets")
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    os.makedirs(TMP_DIR, exist_ok=True)
    os.makedirs(os.path.join(_ASSETS_DIR, "previews"), exist_ok=True)
    app.mount("/output",   StaticFiles(directory=OUTPUT_DIR), name="output")
    app.mount("/tmp-files",StaticFiles(directory=TMP_DIR),    name="tmp")
    app.mount("/assets",   StaticFiles(directory=_ASSETS_DIR),name="assets")


_mount_static()


# ---------------------------------------------------------------- health

@app.get("/api/health")
def health():
    return {"status": "ok"}
