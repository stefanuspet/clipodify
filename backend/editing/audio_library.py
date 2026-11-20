"""Importer audio (8B-1): file dari kotak masuk → library SFX/musik, dengan lisensi WAJIB.

Alur: file diunggah/ditaruh di assets/inbox/ → user mengisi jenis (sfx/musik), tag/mood,
dan lisensi → file dipindah ke assets/sfx atau assets/music + metadata dicatat di YAML.
Lisensi non-komersial (NC) dan tanpa-turunan (ND) ditolak: channel dimonetisasi dan
audio dipadukan ke video (= turunan).
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
from datetime import datetime

import yaml

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
INBOX = os.path.join(_ROOT, "assets", "inbox")
SFX_DIR = os.path.join(_ROOT, "assets", "sfx")
MUSIC_DIR = os.path.join(_ROOT, "assets", "music")
SFX_META = os.path.join(SFX_DIR, "sfx_meta.yaml")
MUSIC_META = os.path.join(MUSIC_DIR, "music_meta.yaml")
AUDIO_EXT = (".mp3", ".wav", ".ogg", ".m4a", ".flac")

LICENSES = {
    "youtube_audio_library": "YouTube Audio Library",
    "cc0": "CC0 (domain publik)",
    "cc_by": "CC BY (wajib atribusi)",
    "pixabay": "Pixabay Content License",
    "own": "Buatan sendiri",
    "other": "Lainnya (jelaskan di catatan)",
}
_FORBIDDEN = re.compile(r"\b(nc|non[- ]?commercial|nd|no[- ]?deriv)", re.IGNORECASE)


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")[:40] or "audio"


def _sha1(path: str) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _duration(path: str) -> float:
    import subprocess
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "default=noprint_wrappers=1:nokey=1", path], capture_output=True, text=True)
    try:
        return round(float(r.stdout.strip()), 2)
    except ValueError:
        return 0.0


def _library_hashes() -> dict[str, str]:
    out = {}
    for d in (SFX_DIR, MUSIC_DIR):
        if os.path.isdir(d):
            for n in os.listdir(d):
                if n.lower().endswith(AUDIO_EXT):
                    out[_sha1(os.path.join(d, n))] = n
    return out


def list_inbox() -> list[dict]:
    os.makedirs(INBOX, exist_ok=True)
    lib = _library_hashes()
    out = []
    for n in sorted(os.listdir(INBOX)):
        p = os.path.join(INBOX, n)
        if not (os.path.isfile(p) and n.lower().endswith(AUDIO_EXT)):
            continue
        dup = lib.get(_sha1(p))
        out.append({"name": n, "size_kb": round(os.path.getsize(p) / 1024, 1), "duration_s": _duration(p),
                    "duplicate_of": dup})
    return out


def save_upload(filename: str, content: bytes) -> str:
    ext = os.path.splitext(filename or "")[1].lower()
    if ext not in AUDIO_EXT:
        raise ValueError("Format tidak didukung. Gunakan mp3/wav/ogg/m4a/flac.")
    os.makedirs(INBOX, exist_ok=True)
    base = _slug(os.path.splitext(os.path.basename(filename))[0])
    name, k = f"{base}{ext}", 1
    while os.path.exists(os.path.join(INBOX, name)):
        k += 1
        name = f"{base}_{k}{ext}"
    with open(os.path.join(INBOX, name), "wb") as f:
        f.write(content)
    return name


def validate_license(lic: dict) -> dict:
    kind = lic.get("kind")
    if kind not in LICENSES:
        raise ValueError("Pilih jenis lisensi.")
    text = " ".join(str(lic.get(k) or "") for k in ("type", "notes"))
    if _FORBIDDEN.search(text):
        raise ValueError("Lisensi non-komersial (NC) atau tanpa-turunan (ND) tidak boleh dipakai.")
    source = (lic.get("source") or "").strip() or LICENSES[kind]
    if kind == "other" and not (lic.get("notes") or "").strip():
        raise ValueError("Lisensi 'Lainnya' wajib diberi penjelasan.")
    attr = kind == "cc_by" or bool(lic.get("attribution_required"))
    attr_text = (lic.get("attribution_text") or "").strip()
    if attr and not attr_text:
        raise ValueError("Lisensi ini wajib atribusi — isi teks kreditnya (salin dari sumber).")
    return {"kind": kind, "type": LICENSES[kind] if kind != "other" else (lic.get("type") or "Lainnya"),
            "source": source, "url": (lic.get("url") or "").strip(),
            "attribution_required": attr, "attribution_text": attr_text,
            "notes": (lic.get("notes") or "").strip(),
            "imported_at": datetime.now().isoformat(timespec="seconds")}


def _read_yaml(path: str) -> tuple[str, dict]:
    if not os.path.exists(path):
        return "", {}
    with open(path, encoding="utf-8") as f:
        text = f.read()
    return text, yaml.safe_load(text) or {}


def import_file(name: str, kind: str, info: dict, lic: dict) -> dict:
    src = os.path.join(INBOX, os.path.basename(name))
    if not os.path.isfile(src):
        raise ValueError("File tidak ada di kotak masuk.")
    if kind not in ("sfx", "music"):
        raise ValueError("Jenis harus sfx atau music.")
    dup = _library_hashes().get(_sha1(src))
    if dup:
        raise ValueError(f"File ini sudah ada di library sebagai {dup}.")
    license_ = validate_license(lic)
    ext = os.path.splitext(name)[1].lower()

    if kind == "sfx":
        raw_tag = (info.get("tag") or "").strip()
        tag = re.sub(r"[^a-z0-9]+", "_", raw_tag.lower()).strip("_")[:30]
        if not tag:
            raise ValueError("Isi tag SFX (mis. whoosh, hit, laugh).")
        sid, k = f"sfx_{tag}_{_slug(info.get('name') or os.path.splitext(name)[0])}", 1
        while os.path.exists(os.path.join(SFX_DIR, sid + ext)):
            k += 1
            sid = f"sfx_{tag}_{k}"
        dest = os.path.join(SFX_DIR, sid + ext)
        shutil.move(src, dest)
        entry = {"name": (info.get("name") or sid).strip()[:60], "tag": tag,
                 "use": (info.get("use") or "").strip()[:160],
                 "intensity": info.get("intensity") if info.get("intensity") in ("low", "mid", "high") else "mid",
                 "source": license_["source"], "license": license_}
        if info.get("align") == "end":
            entry["align"] = "end"
        # Tambahkan di akhir file (format & komentar entri lama tidak disentuh)
        with open(SFX_META, "a", encoding="utf-8") as f:
            f.write("\n" + yaml.safe_dump({sid: entry}, allow_unicode=True, sort_keys=False, width=200))
        return {"kind": "sfx", "id": sid, "file": os.path.basename(dest), "tag": tag}

    # musik
    from backend.editing.music import MOODS
    moods = [m for m in (info.get("mood") or []) if m in MOODS]
    if not moods:
        raise ValueError("Pilih minimal satu mood musik.")
    fname, k = f"{_slug(info.get('title') or os.path.splitext(name)[0])}{ext}", 1
    while os.path.exists(os.path.join(MUSIC_DIR, fname)):
        k += 1
        fname = f"{_slug(info.get('title') or name)}_{k}{ext}"
    dest = os.path.join(MUSIC_DIR, fname)
    shutil.move(src, dest)
    try:
        from tools.index_music import analyze
        stats = analyze(dest)
    except Exception:
        stats = {"duration_s": _duration(dest)}
    text, data = _read_yaml(MUSIC_META)
    tracks = data.get("tracks") or []
    tracks.append({"file": fname, "title": (info.get("title") or fname).strip()[:80], "mood": moods,
                   "energy": info.get("energy") if info.get("energy") in ("low", "mid", "high") else stats.get("energy", "mid"),
                   "bpm": stats.get("bpm"), "duration_s": stats.get("duration_s"),
                   "license": {k2: license_[k2] for k2 in ("source", "url", "type", "attribution_required",
                                                          "attribution_text", "notes", "imported_at")}})
    header = text.split("\ntracks:", 1)[0] + "\n" if "\ntracks:" in text else ""
    with open(MUSIC_META, "w", encoding="utf-8") as f:
        f.write(header)
        yaml.safe_dump({"tracks": tracks}, f, allow_unicode=True, sort_keys=False, width=200)
    return {"kind": "music", "file": fname, "mood": moods}


def trash_sfx(path: str) -> None:
    trash = os.path.join(SFX_DIR, ".trash")
    os.makedirs(trash, exist_ok=True)
    shutil.move(path, os.path.join(trash, os.path.basename(path)))
