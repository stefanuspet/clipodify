"""Impor audio dari assets/inbox ke library SFX/musik (lisensi wajib).

Contoh:
  python tools/import_audio.py --list
  python tools/import_audio.py whoosh_besar.mp3 --kind sfx --tag whoosh --license youtube_audio_library
  python tools/import_audio.py lagu.mp3 --kind music --mood playful upbeat --license cc_by \\
      --url https://… --attribution "Judul oleh X, CC BY 4.0"

Lisensi: youtube_audio_library | cc0 | cc_by | pixabay | own | other (wajib --notes)
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from backend.editing.audio_library import LICENSES, import_file, list_inbox  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("name", nargs="?", help="nama file di assets/inbox")
    ap.add_argument("--list", action="store_true", help="tampilkan isi kotak masuk")
    ap.add_argument("--kind", choices=["sfx", "music"])
    ap.add_argument("--tag", help="SFX: tag (whoosh, hit, laugh, …)")
    ap.add_argument("--title", help="nama tampilan / judul lagu")
    ap.add_argument("--use", help="SFX: kapan dipakai (dibaca Gemini)")
    ap.add_argument("--intensity", choices=["low", "mid", "high"], default="mid")
    ap.add_argument("--riser", action="store_true", help="SFX: akhir file jatuh di momen (riser)")
    ap.add_argument("--mood", nargs="*", default=[])
    ap.add_argument("--energy", choices=["low", "mid", "high"])
    ap.add_argument("--license", required=False, choices=list(LICENSES))
    ap.add_argument("--source", default="")
    ap.add_argument("--url", default="")
    ap.add_argument("--attribution", default="", help="teks kredit (wajib untuk CC BY)")
    ap.add_argument("--notes", default="")
    a = ap.parse_args()

    if a.list or not a.name:
        for f in list_inbox():
            dup = f" (sudah ada: {f['duplicate_of']})" if f["duplicate_of"] else ""
            print(f"- {f['name']}  {f['duration_s']} dtk  {f['size_kb']} KB{dup}")
        return
    if not a.kind or not a.license:
        ap.error("--kind dan --license wajib diisi")
    info = ({"tag": a.tag, "name": a.title, "use": a.use, "intensity": a.intensity,
             "align": "end" if a.riser else None} if a.kind == "sfx"
            else {"title": a.title, "mood": a.mood, "energy": a.energy})
    lic = {"kind": a.license, "source": a.source, "url": a.url, "attribution_text": a.attribution,
           "attribution_required": bool(a.attribution), "notes": a.notes}
    try:
        print(import_file(a.name, a.kind, info, lic))
    except ValueError as exc:
        sys.exit(f"Gagal: {exc}")


if __name__ == "__main__":
    main()
