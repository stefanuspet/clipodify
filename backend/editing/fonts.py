"""Font dari Google Fonts (semua berlisensi terbuka: OFL / Apache 2.0).

Katalog: Google Fonts Developer API (butuh API key di Settings).
Unduhan: file TTF dari katalog; bila key belum diisi, lewat layanan CSS Google Fonts
(tanpa key) dengan daftar font dasar — supaya aplikasi tetap bisa menulis teks.
File disimpan di assets/fonts/gf/ dan dipakai renderer ASS sebagai fontsdir.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time

import requests

logger = logging.getLogger(__name__)

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
FONT_DIR = os.path.join(_ROOT, "assets", "fonts", "gf")
MANIFEST = os.path.join(FONT_DIR, "fonts.json")
CATALOG_CACHE = os.path.join(_ROOT, "tmp", "google_fonts_catalog.json")
CATALOG_TTL_S = 24 * 3600

API_URL = "https://www.googleapis.com/webfonts/v1/webfonts"
CSS_URL = "https://fonts.googleapis.com/css2"
# User-agent lama → Google Fonts CSS mengembalikan TTF (bukan woff2) yang bisa dibaca libass
_TTF_UA = "Mozilla/5.0 (Windows NT 6.1; WOW64; Trident/7.0; rv:11.0) like Gecko"

# Dipakai saat API key belum diisi. Semua tersedia di Google Fonts.
BASE_FAMILIES = [
    ("Anton", "sans-serif", ["400"]),
    ("Bebas Neue", "display", ["400"]),
    ("Montserrat", "sans-serif", ["600", "700", "800", "900"]),
    ("Poppins", "sans-serif", ["600", "700", "800", "900"]),
    ("Archivo Black", "sans-serif", ["400"]),
    ("Bangers", "display", ["400"]),
    ("Luckiest Guy", "display", ["400"]),
    ("Lilita One", "display", ["400"]),
    ("Oswald", "sans-serif", ["500", "600", "700"]),
    ("Barlow Condensed", "sans-serif", ["600", "700", "800", "900"]),
    ("Rubik", "sans-serif", ["600", "700", "800", "900"]),
    ("Titan One", "display", ["400"]),
]


def api_key() -> str:
    from db.database import get_setting
    return get_setting("google_fonts_api_key") or os.getenv("GOOGLE_FONTS_API_KEY", "")


def clear_catalog_cache() -> None:
    if os.path.exists(CATALOG_CACHE):
        os.remove(CATALOG_CACHE)


def fetch_catalog(force: bool = False) -> list[dict]:
    """Seluruh katalog Google Fonts (urut popularitas). Error jelas kalau key kosong/salah."""
    if not force and os.path.exists(CATALOG_CACHE) and time.time() - os.path.getmtime(CATALOG_CACHE) < CATALOG_TTL_S:
        with open(CATALOG_CACHE, encoding="utf-8") as f:
            return json.load(f)
    key = api_key()
    if not key:
        raise ValueError("Google Fonts API key belum diisi di Settings.")
    r = requests.get(API_URL, params={"key": key, "sort": "popularity"}, timeout=20)
    if r.status_code != 200:
        msg = (r.json().get("error") or {}).get("message", r.text[:200]) if r.headers.get(
            "content-type", "").startswith("application/json") else r.text[:200]
        raise ValueError(f"Google Fonts API menolak: {msg}")
    items = r.json().get("items", [])
    os.makedirs(os.path.dirname(CATALOG_CACHE), exist_ok=True)
    with open(CATALOG_CACHE, "w", encoding="utf-8") as f:
        json.dump(items, f)
    return items


def _weights(item: dict) -> list[str]:
    out = []
    for v in item.get("variants", []):
        if "italic" in v:
            continue
        out.append("400" if v == "regular" else v)
    return sorted(set(out))


def curated(limit: int = 60) -> list[dict]:
    """Font yang layak untuk caption: latin, populer, tebal (atau display). Tanpa key → BASE_FAMILIES."""
    try:
        items = fetch_catalog()
    except Exception:
        return [{"family": f, "category": c, "weights": w} for f, c, w in BASE_FAMILIES]
    out = []
    for it in items:
        if "latin" not in it.get("subsets", []) or it.get("category") not in ("sans-serif", "display", "handwriting"):
            continue
        ws = _weights(it)
        if it["category"] != "display" and not any(int(w) >= 700 for w in ws):
            continue
        out.append({"family": it["family"], "category": it["category"], "weights": ws})
        if len(out) >= limit:
            break
    base = {f for f, _, _ in BASE_FAMILIES}
    names = {x["family"] for x in out}
    out += [{"family": f, "category": c, "weights": w} for f, c, w in BASE_FAMILIES if f not in names and f in base]
    return out


def _slug(family: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "", family)


def _load_manifest() -> dict:
    if os.path.exists(MANIFEST):
        with open(MANIFEST, encoding="utf-8") as f:
            return json.load(f)
    return {}


def _ass_name(path: str, weight: str, family: str) -> str:
    """Nama yang dicari libass: nama lengkap dari tabel name font (mis. 'Montserrat Black')."""
    from fontTools.ttLib import TTFont
    names = TTFont(path)["name"]
    full = names.getDebugName(4)
    fam = names.getDebugName(1) or family
    return fam if weight == "400" or not full else full


def _url_from_catalog(family: str, weight: str) -> tuple[str, str] | None:
    try:
        items = fetch_catalog()
    except Exception:
        return None
    it = next((x for x in items if x["family"].lower() == family.lower()), None)
    if not it:
        return None
    ws = _weights(it)
    if not ws:
        return None
    w = min(ws, key=lambda x: abs(int(x) - int(weight)))
    return it["files"]["regular" if w == "400" else w], w


def _url_from_css(family: str, weight: str) -> tuple[str, str]:
    r = requests.get(CSS_URL, params={"family": f"{family}:wght@{weight}"},
                     headers={"User-Agent": _TTF_UA}, timeout=20)
    if r.status_code != 200 and weight != "400":
        r = requests.get(CSS_URL, params={"family": family}, headers={"User-Agent": _TTF_UA}, timeout=20)
        weight = "400"
    if r.status_code != 200:
        raise ValueError(f"Font '{family}' tidak ditemukan di Google Fonts")
    m = re.search(r"url\((https://[^)]+)\)", r.text)
    if not m:
        raise ValueError(f"Font '{family}' tidak punya file yang bisa diunduh")
    return m.group(1), weight


def ensure_font(family: str, weight: str = "400") -> dict:
    """Pastikan file font ada di cache; return {family, weight, path, ass_name}."""
    weight = str(weight)
    manifest = _load_manifest()
    for rec in manifest.values():
        if rec["family"].lower() == family.lower() and rec["requested"] == weight and os.path.exists(rec["path"]):
            return rec
    found = _url_from_catalog(family, weight)
    url, real_w = found if found else _url_from_css(family, weight)
    os.makedirs(FONT_DIR, exist_ok=True)
    path = os.path.join(FONT_DIR, f"{_slug(family)}-{real_w}.ttf")
    if not os.path.exists(path):
        r = requests.get(url, timeout=30)
        r.raise_for_status()
        with open(path, "wb") as f:
            f.write(r.content)
        logger.info("Font diunduh: %s %s (%d KB)", family, real_w, len(r.content) // 1024)
    rec = {"family": family, "requested": weight, "weight": real_w, "path": path,
           "ass_name": _ass_name(path, real_w, family), "source": "Google Fonts",
           "license": "SIL Open Font License / Apache 2.0 (Google Fonts)"}
    manifest[f"{_slug(family)}-{weight}"] = rec
    with open(MANIFEST, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)
    return rec
