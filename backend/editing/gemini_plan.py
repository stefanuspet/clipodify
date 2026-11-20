"""Satu panggilan Gemini per klip: context pack → EditPlan (JSON terkunci skema)."""

from __future__ import annotations

import json
import logging

import json as _json

from backend.editing.plan_schema import catalog, response_schema
from backend.editing.rhythm import base_rhythm, describe as describe_rhythm

logger = logging.getLogger(__name__)

SYSTEM_BASE = """Kamu editor video pendek (TikTok/Reels/Shorts) untuk klip podcast Indonesia.
Tugasmu menyusun RENCANA EDIT untuk satu klip, memakai HANYA aset yang tersedia,
mengikuti PERSONA channel dan PLAYBOOK di bawah.

Sasaran utama: penonton bereaksi — membagikan ke teman, menandai orang, atau menulis
komentar. Retensi channel sudah sehat; yang kurang adalah share dan komentar.

Urutan kerja (ikuti urutan field JSON):
0. `people`: beri nama tiap orang (ID1, ID2, …) dari BUKTI: judul video, nama yang
   disebut/disapa di transkrip, siapa yang bicara di shot mana, dan kalimat yang dia
   ucapkan. JANGAN menebak dari penampilan. Kalau tidak ada bukti nama, isi name
   dengan peran ("Host", "Tamu pria", …) dan confidence rendah (< 0.5).
1. `beats`: petakan momen klip dari transkrip + emosi audio. Setiap beat = rentang nomor
   kata + tipe + catatan singkat kenapa momen itu penting. Tandai tawa (emosi audio
   'laughing'), punchline, kalimat kunci, puncak emosi, twist. 4–15 beat biasanya cukup.
2. `intent`: dari isi beat, bukan dari judul saja.
3. Pilih edit dengan resep playbook untuk intent itu, ditambatkan ke beat:
   SFX/teks/efek/highlight jatuh di kata di dalam beat yang sesuai.

Semua waktu ditulis sebagai NOMOR KATA (#n) dari transkrip. Jangan menebak detik.
`offset_s` SFX boleh negatif (sampai -2 detik) untuk suara build-up yang memuncak di kata itu.

Tool yang mengubah timeline:
- `cuts.remove_silence`: padatkan jeda panjang antar kata. Hampir selalu true, kecuali
  jeda itu sendiri bagian dari komedi (diam canggung) atau momen haru.
- `cuts.remove_fillers`: buang kata pengisi (eh, ehm, hmm). Biasanya true.
- `cuts.ranges`: buang bagian yang jelas lemah (basa-basi, pengulangan, salah ucap),
  maks 30 kata per potongan. JANGAN memotong punchline, kalimat kunci, twist, payoff,
  atau tawa. Kosongkan bila ragu — klip sudah dipilih karena utuh.
- `hook_teaser`: ambil 1.5–6 detik momen terkuat (punchline/kalimat paling memancing)
  dan putar sebagai cuplikan di detik 0, lalu klip berjalan dari awal seperti biasa.
  Pakai HANYA bila momen terkuat ada setelah detik 3 dan pembuka klip kurang kuat.

Tool visual & audio:
- `build_ups`: untuk 1–2 MOMEN BESAR saja (reveal, kalimat kunci paling kuat, punchline
  terbesar). Riser makin keras lalu berhenti TEPAT di awal kata `to_word`, lalu `hit` menghantam
  di kata itu. length short = 1 dtk, long = 2 dtk (butuh ruang ucapan sebelumnya). hit impact =
  tegas, bass_drop = dramatis/berat, none = riser saja. Jangan di tawa atau momen haru. Jangan
  taruh SFX lain di dekatnya (akan dibuang otomatis).
- Celah >6 dtk tanpa perubahan apa pun (di luar momen tenang/haru) akan diisi otomatis dengan
  zoom kecil atau SFX ringan di kata highlight. Lebih baik kamu isi sendiri dengan pilihan yang
  bermakna (zoom, SFX, teks) — lihat RITME BAWAAN.
- `zooms`: hanya untuk momen di shot berisi 1 orang (lihat SHOT KAMERA). `punch`
  (scale 1.12–1.2, 0.6–1.5 detik) untuk punchline/reveal/kata terkeras; `slow`
  (1.05–1.10) sepanjang kalimat kunci. Zoom di shot multi-orang dibuang otomatis.
- `emojis`: 0–3 stiker emoji yang muncul sebentar (pop) di samping, tepat di kata `word_index`.
  Untuk reaksi yang jelas: tawa (😂🤣💀), kaget (😱😳🤯), setuju/keren (🔥💯👏), haru (🥲🙏❤️),
  bingung (🤔👀). Jangan emoji lucu di momen haru. Jarak antar emoji ≥4 detik. Kosongkan bila tidak perlu.
- `sfx.tag`: pilih jenis suara (lihat daftar SFX), bukan nama file.
- `sfx.volume`: semua SFX sudah disamakan levelnya, jadi volume = rasa: 1.0 = jelas terdengar di
  bawah suara orang, 0.6 = aksen halus, 0.3 = nyaris tak terasa, >1.0 hanya untuk momen terbesar.
- `music`: musik latar pelan di bawah suara (otomatis mengecil saat ada yang bicara).
  volume_db -24 (sangat pelan) s/d -16 (terasa). Lihat status library di ASET.
- `end_card`: frame terakhir dibekukan + digelapkan + teks besar 1.5–3.5 detik. Cocok
  untuk pertanyaan penutup yang kuat (lalu teks `question` biasa tidak perlu lagi).

Aturan umum:
- Teks overlay pendek (maks 6 kata), menambah nilai, tidak mengulang subtitle.
- Sangat dianjurkan: SATU teks `question` di 3–5 detik terakhir — pertanyaan SPESIFIK
  dari isi klip yang orang ingin jawab atau yang membuat mereka menandai teman.
- `hook_title` hanya bila 3 detik pertama butuh konteks; taruh di #0, durasi 2–3 detik.
- `subtitle.highlight_words`: kata punchline, angka, kata emosional, atau inti kalimat
  kunci. Maksimal 1 per kalimat, bukan kata sambung.
- Posisi subtitle dan teks digeser otomatis supaya tidak menutupi wajah; pilih posisi
  sesuai gaya saja. Subtitle biasanya bottom.

SUBTITLE = RESEP YANG KAMU RANCANG (bukan template):
- Mulai dari GAYA FAVORIT channel yang paling cocok dengan intent (isi `base_style_id`),
  salin resepnya ke `recipe`, lalu boleh kamu variasikan supaya pas dengan klip ini:
  warna (fill_colors, color_mode), animasi, ukuran, kata per baris, kata aktif, highlight.
  Pertahankan ciri khas gaya favorit (mis. outline tebal, satu kata) kecuali klip butuh beda.
- Kalau tidak ada favorit yang cocok, rancang baru (`base_style_id` = "baru").
- Arti pengaturan: size = tinggi font px di kanvas 1080×1920 (caption besar 110–150);
  words_per_line 1 = satu kata per layar (energik), 3–4 = lebih tenang/terbaca;
  color_mode cycle_word = warna berganti tiap kata dari fill_colors; outline.width relatif
  ukuran font (0.2 = sangat tebal); animation.type pop/bounce = energik, fade/slide_up = tenang,
  wipe = dramatis; active_word = gaya kata yang sedang diucapkan (untuk words_per_line > 1).
- Keterbacaan wajib: teks terang butuh outline/bayangan gelap atau kotak.

WARNA: gunakan data WARNA TAMPILAN AKHIR. Pilih warna subtitle yang kontras dengan warna
  dominan area posisinya (jangan teks coklat/krem di atas area krem). Grade: kalau video sudah
  HANGAT jangan tambah warmth (malah boleh sedikit negatif); kalau sudah terang jangan naikkan
  brightness; kalau kulit sudah jenuh jangan naikkan saturation. Grade disimulasikan setelahnya
  dan dikurangi otomatis bila merusak — jadi pilih yang halus.
GRADE = RESEP WARNA: contrast 0.9–1.4, saturation 0.7–1.5, brightness -0.1–0.1, gamma 0.85–1.15,
  warmth -1 (dingin/biru) … +1 (hangat/oranye), tint -1 (magenta) … +1 (hijau). Halus saja:
  warmth/tint biasanya di antara -0.4 dan 0.4. Semua 1/0 = tanpa grade.
- framing: grid_auto (rekomendasi); center_crop hanya bila grid tidak cocok.
- Warna hex (#RRGGBB), kontras dengan video; utamakan palet persona.
- `reasoning`: 2–3 kalimat — intent, momen kunci, kenapa pilihan ini memancing reaksi.
"""


def build_system_prompt(persona: dict) -> str:
    from backend.editing.persona import examples_prompt, load_playbook, persona_prompt, playbook_prompt
    parts = [SYSTEM_BASE, "PERSONA CHANNEL\n" + persona_prompt(persona),
             "PLAYBOOK\n" + playbook_prompt(load_playbook())]
    ex = examples_prompt(persona)
    if ex:
        parts.append("CONTOH PLAN YANG TERBUKTI BAGUS DI CHANNEL INI (tiru rasanya, bukan isinya)\n" + ex)
    return "\n\n".join(parts)


def _fmt_words(ctx: dict) -> str:
    words = ctx["words"]
    lines = []
    for sen in ctx["sentences"]:
        toks = " ".join(f"#{w['i']} {w['w']}" for w in words[sen["from_word"]:sen["to_word"] + 1])
        lines.append(f"[{sen['s']:.1f}–{sen['e']:.1f}s] {toks}")
    return "\n".join(lines)


def _fmt_emotions(ctx: dict) -> str:
    rows = [
        f"- {e['s']:.1f}–{e['e']:.1f}s {e['emotion']} (intensitas {e['intensity']})"
        for e in ctx["audio_emotions"]
        if e["emotion"] not in ("neutral", "silence")
    ]
    return "\n".join(rows) or "- (tidak ada emosi menonjol)"


def _fmt_shots(ctx: dict) -> str:
    rows = []
    for k, sh in enumerate(ctx["shots"], 1):
        n = len([p for p in sh["persons"] if p != "_none"])
        ids = sorted(set((sh.get("identity") or {}).values()))
        desc = "tidak ada orang" if n == 0 else f"{n} orang ({sh['layout']})" + (f": {', '.join(ids)}" if ids else "")
        rows.append(f"- shot {k}: {sh['t_start']:.1f}–{sh['t_end']:.1f}s, {desc}")
    return "\n".join(rows)


def _fmt_people(ctx: dict) -> str:
    rows = []
    for p in ctx.get("people") or []:
        shots = ", ".join(str(k + 1) for k in p["shots"])
        line = f"- {p['id']}: tampil {p['screen_s']:.0f} dtk (shot {shots}), bicara ±{p['speak_s']:.0f} dtk"
        if p["lines"]:
            line += "; contoh ucapannya: " + " | ".join(f'"{x}"' for x in p["lines"][:2])
        rows.append(line)
    return "\n".join(rows) or "- (tidak ada wajah yang dikenali)"


def _fmt_colors(ctx: dict) -> str:
    cols = ctx.get("colors") or []
    if not cols:
        return "- (tidak ada data)"
    def avg(k):
        return sum(c[k] for c in cols) / len(cols)
    w = avg("warm_cast")
    rows = [f"- Keseluruhan: kecerahan {avg('luma'):.2f} (0–1), kontras {avg('contrast'):.2f}, saturasi "
            f"{avg('saturation'):.2f}, cast {'HANGAT' if w > 0.08 else 'DINGIN' if w < -0.05 else 'netral'} "
            f"({w:+.2f}), highlight terpotong {avg('clip_high') * 100:.1f}%"]
    skins = [c["skin"] for c in cols if c.get("skin")]
    if skins:
        rows.append(f"- Kulit wajah: saturasi {sum(s['saturation'] for s in skins) / len(skins):.2f} "
                    f"(>0.6 mulai terlihat terlalu oranye), contoh {skins[0]['hex']}")
    for c in cols:
        areas = "; ".join(f"{name}: " + ", ".join(f"{x['name']} {x['hex']} {int(x['share'] * 100)}%" for x in v[:2])
                          for name, v in c["areas"].items() if name in ("bottom", "center", "top") or name.startswith("seam"))
        rows.append(f"- shot {c['shot'] + 1}: {areas}")
    return "\n".join(rows)


def build_prompt(ctx: dict, cat: dict) -> str:
    m = ctx["meta"]
    sfx_lines = "\n".join(f"- {t['tag']}: {t['use']}" for t in cat["sfx_tags"])
    music_line = (f"tersedia untuk mood: {', '.join(cat['music_available'])}" if cat["music_available"]
                  else "library musik berlisensi MASIH KOSONG — set music.enabled=false")
    style_lines = "\n".join(
        f"- id `{st['id']}` — {st['name']}{' (FAVORIT)' if st.get('favorite') else ''}"
        f"{': ' + st['notes'] if st.get('notes') else ''}\n  resep: {_json.dumps(st['recipe'], ensure_ascii=False)}"
        for st in cat["styles"] if st.get("favorite")) or "- (belum ada gaya favorit — rancang baru)"
    font_lines = ", ".join(f"{f['family']} [{'/'.join(f['weights'])}]" for f in cat["font_families"])
    return f"""DATA KLIP
- Judul video sumber: {m.get('video_title') or '-'}
- Judul klip: {m.get('title') or '-'}
- Hook: {m.get('hook') or '-'}
- Tipe (dari analisis awal): {m.get('clip_type') or '-'}
- Alasan dipilih: {m.get('reason') or '-'}
- Orang disebut: {', '.join(m.get('people') or []) or '-'}
- Mode: {m.get('mode') or 'regular'}{' · produk: ' + m['campaign_product'] if m.get('campaign_product') else ''}
- Durasi: {ctx['duration']:.1f}s, jumlah kata: {len(ctx['words'])} (#0–#{len(ctx['words']) - 1})

TRANSKRIP PER KATA (#nomor kata, [waktu kalimat])
{_fmt_words(ctx)}

EMOSI AUDIO
{_fmt_emotions(ctx)}

SHOT KAMERA
{_fmt_shots(ctx)}

RITME BAWAAN (sebelum diedit; hanya cut kamera — subtitle tidak dihitung)
{describe_rhythm(base_rhythm(ctx), "Klip asli")}
Video pendek terasa modern bila ada perubahan (zoom, SFX, teks, B-roll, cut) kira-kira tiap 2–4 detik,
TAPI hanya di momen yang pantas — momen tenang/haru boleh dibiarkan diam.

ORANG DI VIDEO (dikenali dari wajah, konsisten lintas shot)
{_fmt_people(ctx)}

WARNA TAMPILAN AKHIR (diukur dari kanvas 1080×1920 per shot; area = tempat subtitle bisa jatuh)
{_fmt_colors(ctx)}

ASET TERSEDIA
Gaya subtitle favorit (dari Studio Subtitle):
{style_lines}
Font (Google Fonts, [ketebalan tersedia]): {font_lines}
Efek visual berwaktu: {', '.join(cat['effects'])}
SFX (tag: kegunaan):
{sfx_lines}
Musik latar: {music_line}
Font teks overlay: pilih dari daftar font di atas.

Susun rencana edit untuk klip ini."""


def generate_plan(ctx: dict, persona: dict) -> tuple[dict, str]:
    """Return (plan mentah dari Gemini, nama model). Satu panggilan: beat + rencana edit."""
    from google.genai import types
    import core.gemini_client as gemini_client
    from core.gemini_analyzer import _raise_if_truncated

    cat = catalog()
    config = types.GenerateContentConfig(
        system_instruction=build_system_prompt(persona),
        response_mime_type="application/json",
        response_schema=response_schema(cat, [p["id"] for p in ctx.get("people") or []]),
        temperature=0.6,
        max_output_tokens=8192,
    )
    response, model = gemini_client.generate(build_prompt(ctx, cat), config)
    _raise_if_truncated(response)
    return json.loads(response.text), model
