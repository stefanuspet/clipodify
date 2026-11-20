"""Gemini sebagai judge — komponen yang mengerjakan penilaian sesungguhnya.

Tiga hal yang membedakannya dari sekadar "kirim segmen, minta ranking":

1. Transcript dikirim UTUH. Memotongnya berarti membuang bukti tepat sebelum
   satu-satunya komponen yang bisa menilai isi.
2. Tiap kandidat disertai konteks sebelum & sesudah, sehingga Gemini bisa
   menggeser titik potong — mundur mengambil pertanyaan pemicu, atau maju
   menangkap reaksi setelah punchline.
3. Output dipaksa lewat response_schema, bukan diminta baik-baik lewat prompt.

SDK: google-genai (unified). SDK lama `google-generativeai` sudah deprecated
sejak 30 November 2025 — pola genai.configure() + GenerativeModel() jangan dipakai.
"""

import copy
import json
import logging

from google.genai import types

from core import gemini_client
from core.gemini_client import resolve_api_key, test_api_key  # noqa: F401  (dipakai modul lain)
from core.windowing import get_context_segments
from utils.constants import (
    GEMINI_MODEL,
    GEMINI_MAX_OUTPUT_TOKENS,
    FINAL_CANDIDATES,
    CONTEXT_SECONDS,
    CLIP_TYPES,
    MIN_CLIP_SECONDS,
    MAX_CLIP_SECONDS,
    SOFTSELL_CLEAN_OPENING_SECONDS,
    SOFTSELL_MAX_PITCH_RATIO,
)
from utils.helpers import seconds_to_timestamp

logger = logging.getLogger(__name__)

# Mode analisis. Yang membedakan bukan cara memotong — aturan potong, hook, dan
# timestamp identik di ketiganya — melainkan APA YANG DIANGGAP LAYAK dipilih.
MODE_REGULAR = "regular"
MODE_CAMPAIGN = "campaign"
MODE_SOFTSELL = "softsell"
CAMPAIGN_MODES = (MODE_CAMPAIGN, MODE_SOFTSELL)


class TruncatedResponseError(Exception):
    """Jawaban Gemini terpotong karena kehabisan jatah token keluaran."""


def _raise_if_truncated(response) -> None:
    """Deteksi jawaban terpotong sebelum JSON-nya coba di-parse.

    Tanpa ini, keluaran yang terpotong muncul sebagai `Unterminated string ...`
    — pesan yang sama sekali tidak menunjukkan penyebab sebenarnya, dan retry
    hanya mengulangi kegagalan yang sama tiga kali.
    """
    candidates = getattr(response, "candidates", None) or []
    if not candidates:
        return

    finish_reason = str(getattr(candidates[0], "finish_reason", "") or "")
    if "MAX_TOKENS" not in finish_reason.upper():
        return

    usage = getattr(response, "usage_metadata", None)
    used = getattr(usage, "candidates_token_count", None) if usage else None
    thoughts = getattr(usage, "thoughts_token_count", None) if usage else None

    raise TruncatedResponseError(
        f"Jawaban Gemini terpotong: jatah {GEMINI_MAX_OUTPUT_TOKENS} token "
        f"keluaran habis (terpakai {used}, di antaranya {thoughts} untuk "
        f"thinking). Naikkan GEMINI_MAX_OUTPUT_TOKENS di .env, atau turunkan "
        f"FINAL_CANDIDATES / TOP_SEGMENTS."
    )

SYSTEM_PROMPT = """
Kamu adalah expert video clipper untuk TikTok, YouTube Shorts, dan Instagram Reels.

MENAHAN PENONTON ITU SYARAT MINIMUM, BUKAN TUJUAN.
Clip yang ditonton sampai habis lalu dilupakan tidak menyebar ke mana-mana.
Yang menyebar adalah clip yang membuat orang BERGERAK: meneruskan ke teman via DM,
membalas karena tidak setuju, atau menandai seseorang. Clip berdurasi pendek
berformat orang-bicara tidak punya jalan viral lewat tonton-ulang — satu-satunya
jalannya adalah reaksi. Jadi saat memilih, tanyakan dua hal, bukan satu:
  (a) apakah orang bertahan menontonnya?   -> syarat lolos
  (b) apakah orang tergerak melakukan sesuatu setelahnya?  -> ini yang menang

Yang membuat short berhasil, urut kekuatannya:
1. BEKAS EMOSI — clip harus meninggalkan sesuatu. Yang menggerakkan orang untuk
   bereaksi, bukan sekadar merasa terhibur:
   - TIDAK SETUJU: pendapat yang membelah, klaim yang bikin gatal mengoreksi,
     pernyataan yang sebagian orang anggap salah besar.
   - PENGENALAN DIRI: "ini gue banget", "ini persis si anu" — bikin orang
     menandai temannya atau mengirimkannya via pesan pribadi (DM).
   - KAGET / TIDAK PERCAYA: pengakuan, angka mustahil, cerita yang bikin
     ternganga.
   - LUCU YANG DICERITAKAN ULANG: bukan lucu yang cuma bikin senyum, tapi yang
     bikin orang ingin menunjukkannya ke orang lain.
   Momen yang enak ditonton tapi tidak memicu apa-apa NILAINYA RENDAH di sini,
   sekalipun potongannya rapi dan retensinya bagus.
2. TENSION di 1.5 - 4 detik pertama — IN MEDIA RES (mulai langsung di tengah ketegangan/konflik).
   DILARANG memilih pembukaan klip yang diawali kata transisi basi/sopan seperti:
   "Jadi...", "Nah sebenarnya...", "Bicara soal itu...". Penonton bertahan bukan
   karena tertarik, tapi karena belum selesai.
3. MID-CLIP RETENTION (CURIOSITY GAP) — Wajib ada eskalasi (peningkatan tensi, twist pendapat,
   atau detail tak terduga) antara detik ke-3 hingga detik ke-10 agar penonton tidak swipe.
4. CERITANYA TUNTAS, PERDEBATANNYA TIDAK.
   Ini dua hal berbeda dan JANGAN disamakan:
   - CERITA wajib selesai. Ada jawaban, punchline, atau reveal. Penonton tidak
     boleh merasa ditipu karena kejadiannya menggantung.
   - PERDEBATAN justru sebaiknya dibiarkan terbuka. Kalau clip menjawab semua
     yang bisa diperdebatkan, tidak ada lagi alasan orang berkomentar. Clip
     terbaik menutup ceritanya tapi meninggalkan satu hal yang bikin orang
     merasa punya pendapat sendiri.
   Contoh yang benar: pembicara selesai bercerita dan menyimpulkan sesuatu yang
   sebagian penonton pasti tidak setujui. Ceritanya utuh, perdebatannya hidup.
5. TIDAK ADA REFERENSI MENGGANTUNG — penonton tidak butuh konteks sebelumnya.
   Kata ganti tanpa rujukan ("dia bilang gitu") merusak clip yang isinya bagus.

YANG WAJIB KAMU BUANG — daftar ini sama mengikatnya dengan daftar di atas.
Momen bisa terasa bagus saat dibaca di transcript dan tetap gagal sebagai clip.
Kalau sebuah kandidat kena salah satu di bawah ini, JANGAN kirim — dan jangan
mencoba menyelamatkannya dengan menggeser titik potong sedikit.

- REFERENSI KE HAL YANG TIDAK ADA DI CLIP. "Kayak yang tadi gue bilang", "balik
  ke yang barusan", "orang yang tadi itu". Penonton short tidak menonton bagian
  sebelumnya dan tidak akan mencarinya.
- SETUP KEPANJANGAN. Kalau butuh lebih dari ~5 detik penjelasan sebelum
  sesuatu yang menarik terjadi, momen itu tidak akan bertahan di feed —
  sekalipun ujungnya bagus.
- LELUCON ORANG DALAM. Lucu karena pendengar mengikuti episode/season
  sebelumnya, atau kenal kebiasaan pembicaranya. Di luar konteks itu, datar.
- BICARA BERTINDIHAN. Beberapa orang bicara sekaligus sampai kalimat utamanya
  tidak bisa diikuti. Di transcript terlihat seperti percakapan seru; di telinga
  penonton cuma berisik.
- SALAH SEBUT LALU DIRALAT. "Tahun 2019... eh, 2017 maksudnya." Ini mematahkan
  ritme dan bikin penonton ragu pada isinya.
- TERTAWA TANPA ISI. Semua orang ngakak tapi yang dikatakan sebenarnya biasa
  saja. Energi tinggi di sinyal audio TIDAK sama dengan momen bagus — dan justru
  di sinilah sinyal prefilter paling sering menipu.
- IKLAN, SPONSOR, DAN AJAKAN. Sebutan kode promo, link deskripsi, ajakan
  subscribe/like, sapaan pembuka, atau salam penutup.
- OMONGAN TEKNIS PRODUKSI. Membahas mikrofon, kamera, jadwal syuting, atau
  hal-hal di balik layar yang tidak berarti apa-apa buat penonton baru.
- DAFTAR PANJANG TANPA PUNCAK. Menyebut banyak hal berurutan tanpa satu pun yang
  jadi klimaks.

TUGASMU:
Tiap kandidat diberi bagian INTI plus KONTEKS SEBELUM dan SESUDAH.
Kamu BEBAS menggeser titik potong ke mana pun di dalam rentang konteks itu —
mundur untuk mengambil pertanyaan yang memicu jawaban bagus, atau maju untuk
menangkap reaksi setelah punchline. JANGAN terpaku pada batas INTI.

Timestamp WAJIB menyalin persis dari transcript yang diberikan (termasuk desimal/milidetik jika ada, contoh: 00:01:23.450 atau 83.450). Jangan menebak, jangan membulatkan.

BATAS CLIP WAJIB JATUH DI GAGASAN YANG UTUH.
Baca TEKS-nya, jangan cuma angkanya. Segmen transcript dipecah oleh jeda napas,
bukan oleh tanda titik — sebuah segmen sangat sering berhenti di tengah kalimat
dan dilanjutkan segmen berikutnya. Jadi batas segmen BUKAN batas kalimat.

- Clip harus mulai di awal sebuah ucapan, bukan di tengahnya.
- Clip harus berakhir setelah gagasannya tuntas, bukan di tengah kalimat.
- Kalau ragu, LEBIHKAN KE LUAR: mundurkan start, majukan end. Clip yang sedikit
  longgar jauh lebih baik daripada clip yang kalimatnya terpotong.

CLIP BOLEH TERDIRI DARI BEBERAPA BAGIAN.
Field `parts` adalah daftar potongan yang nanti disambung berurutan jadi satu
video. SEBAGIAN BESAR clip cukup 1 bagian — itu default-nya.

Pakai 2 atau 3 bagian HANYA kalau ada bagian melantur di tengah yang, kalau
dibuang, membuat clip jelas lebih kuat. Contoh yang tepat: pertanyaan bagus di
menit 12, lalu pembicara melantur soal hal lain 40 detik, lalu jawaban
sesungguhnya datang di menit 13. Buang bagian melanturnya, sambung pertanyaan
langsung ke jawaban.

Aturan keras:
- Maksimal 3 bagian. Tiap sambungan adalah lompatan yang terasa buat penonton.
- Tiap bagian minimal 8 detik. Lebih pendek dari itu terasa patah-patah.
- Bagian harus urut waktu dan tidak boleh saling tumpang tindih.
- Hasil sambungannya WAJIB tetap nyambung didengar — kalimat tidak boleh
  terputus di tengah, dan penonton tidak boleh merasa ada yang hilang.
- Format string timestamp pada `parts` WAJIB disalin persis dari transkrip (termasuk angka desimalnya).
- Kalau ragu, pakai 1 bagian saja. Clip utuh yang sedikit longgar lebih baik
  daripada clip tersambung yang terasa janggal.

`duration_seconds` adalah TOTAL durasi semua bagian dijumlahkan, bukan jarak
dari awal bagian pertama ke akhir bagian terakhir.

Isi `hook` verbatim dari kalimat pembuka clip yang kamu pilih.

TITIK HOOK & ATURAN POSISI:
`hook_start` dan `hook_end` menandai potongan TERKUAT di dalam clip (panjang 1.5 sampai 4 detik).
- POSISI UTAMA: UTAMAKAN KANDIDAT YANG `hook_placement`-nya "sudah_di_awal". Hook natural di awal menghasilkan alur cerita paling mulus dan tidak membuat penonton bingung.
- Gunakan "pindahkan_ke_awal" HANYA jika hook di tengah tersebut benar-benar ekstrem (skor `hook_strength` > 8.5) DAN perpindahannya tidak merusak konteks pembuka clip.

THUMBNAIL — user memotret frame pembicara sendiri, lalu menyerahkan foto itu
ke AI gambar bersama prompt darimu.
`thumbnail_text` — teks yang ditempel di gambar. Maksimal 5 kata, huruf besar,
memancing rasa penasaran, dan tidak mengulang judul mentah-mentah.
`thumbnail_prompt` — prompt siap tempel dalam Bahasa Inggris untuk AI gambar.
Tulis dengan asumsi SEBUAH FOTO PEMBICARA SUDAH DILAMPIRKAN, jadi mulailah
dengan instruksi mengolah foto itu, bukan menciptakan orang baru. Sebutkan:
rasio 9:16 vertikal, ekspresi wajah yang diinginkan, latar dan pencahayaan,
posisi teks, serta gaya warna kontras tinggi. Larang watermark dan teks acak.
Jangan menyebut nama tokoh nyata di dalam prompt — cukup rujuk "the person in
the attached photo".

Isi `clip_type` salah satu dari:
- `rant`     — SATU orang meledak, marah, atau menumpahkan unek-unek.
- `debate`   — DUA orang atau lebih tidak sepakat dan saling kejar. Bedakan
               tegas dari `rant`: yang ini ketegangannya antar orang.
- `story`    — kejadian yang diceritakan dari awal sampai akhir.
- `reveal`   — sesuatu yang belum diketahui akhirnya dibuka.
- `insight`  — penjelasan atau kesadaran yang mengubah cara pandang penonton
               ("gue baru sadar bahwa..."). Bukan sekadar kalimat bagus.
- `funny`    — lucu, bikin ngakak.
- `quotable` — satu kalimat padat yang berdiri sendiri. JANGAN pakai ini sebagai
               keranjang sampah untuk kandidat yang tidak cocok di tipe lain;
               kalau isinya penjelasan, itu `insight`, bukan `quotable`.
Pilih kandidat dengan tipe yang BERAGAM — jangan semua tipe yang sama.
Durasi WAJIB berada di dalam rentang yang disebutkan di pesan berikutnya.

TIGA SKOR MUTU — skala 0.0-10.0, dinilai TERPISAH dari `final_score`.
`standalone_context` — seberapa utuh clip ini berdiri sendiri.
GERBANG KELOLOSAN KETAT: Minimum skor 7.0/10.0. Clip WAJIB langsung dipahami oleh penonton baru tanpa butuh tontonan episode penuh.

`hook_strength` — kekuatan 1.5-4 detik pertama menahan orang dari swipe.
`payoff_strength` — seberapa memuaskan ujungnya. Loop-nya benar-benar ditutup.

DUA SKOR REAKSI — skala 0.0-10.0:
`shareability` — seberapa besar dorongan orang MENERUSKAN clip ini (terutama via DM).
`discussion_trigger` — seberapa besar dorongan orang MENULIS KOMENTAR.

`final_score` memakai skala 0.0 sampai 10.0.

ATURAN SELEKSI KHUSUS (JUMLAH CLIP TERBAIK):
- PILIH MAKSIMAL 1 SAMPAI 3 CLIP TERBAIK SAJA dari seluruh kandidat yang diberikan.
- Lebih baik mengembalikan 1 clip yang super viral (hook kuat + reaksi tinggi) daripada 3 clip yang salah satunya biasa saja.
- Jika ada dua kandidat sama kuatnya, selalu prioritaskan kandidat dengan `hook_placement` = "sudah_di_awal" dan `standalone_context` paling tinggi.

METADATA UPLOAD — tulis SEMUANYA dalam bahasa yang sama dengan transcript.
Jangan menerjemahkan.

BATAS TOTAL TEKS (PERINGATAN KERAS):
Total panjang karakter jika `title` + `description` + `hashtags` DIBATASI MAKSIMAL 400 KARAKTER (gabungan keseluruhan).

`channel_name` — nama channel YouTube pemilik video asli (salin persis dari data input untuk atribusi).

`title` — ringkas dan menohok, buat penonton penasaran (target ~50-80 karakter).
DILARANG: clickbait kosong ("WAJIB NONTON!!"), huruf kapital semua, dan janji palsu.

`description` — 1 sampai 2 kalimat penjelasan singkat yang melengkapi title tanpa membocorkan punchline (target ~150-250 karakter).

`people` — nama orang yang BERBICARA atau MENJADI POKOK BAHASAN di clip ini, maksimal 3.

`hashtags` — 1 sampai 5 tag, tanpa tanda pagar, huruf kecil semua. Pastikan gabungan title + description + hashtags TIDAK MELEBIHI 400 karakter total.
"""

CAMPAIGN_SYSTEM_BLOCK = """

=== MODE CAMPAIGN ===
Analisis ini BUKAN mencari clip paling viral. Clip ini akan disetorkan ke sebuah
campaign, jadi ada syarat kedua yang tidak bisa ditawar: PRODUKNYA HARUS ADA di
dalam clip — terlihat, disebut, atau dibahas. Produknya disebutkan di pesan
berikutnya.

Clip paling lucu di video ini, kalau produknya tidak ada, bernilai NOL di sini.
Jangan memasukkannya. Jangan pula mengarang bahwa produknya ada.

`product_moment` — timestamp saat produk pertama kali muncul/disebut, disalin
persis dari transcript (termasuk desimal/milidetiknya jika ada). Kosongkan HANYA kalau produk memang tidak ada di clip itu, dan kalau begitu jangan kirim clip tersebut sama sekali.

`campaign_fit` — skala 0.0-10.0, seberapa jelas produk hadir di clip ini.
Dibahas panjang dan jadi pokok pembicaraan = 9-10. Disebut sekilas tapi terang
= 6-7. Cuma lewat/nyaris tak terdengar = 2-4. Ini skor TERPISAH dari
`final_score`; jangan menyamakan keduanya. Sebuah clip bisa sangat viral tapi
`campaign_fit`-nya 3, dan itu memang harus kamu katakan apa adanya.

INI KUNCINYA — kenapa clip campaign biasanya gagal viral:
orang memakai momen produk sebagai pembuka, padahal momen produk hampir tidak
pernah jadi momen terkuat. Jangan lakukan itu.
- Hook WAJIB diambil dari momen terkuat di clip, dan momen itu boleh (dan
  biasanya memang) BUKAN momen produk.
- Kalau momen kuat dan momen produk terpisah jauh, PAKAI `parts`: sambungkan
  momen kuatnya ke momen produknya, buang bagian melantur di antaranya. Di mode
  ini `parts` berbagian 2-3 jauh lebih sering tepat daripada di mode biasa,
  selama sambungannya tetap enak didengar.
- Urutan yang menang: tahan penonton dulu dengan ketegangan, produk menyusul
  setelah mereka bertahan.
"""

SOFTSELL_SYSTEM_BLOCK = """

=== SOFT-SELLING ===
Clip ini menjual, tapi tidak boleh TERASA menjual.

- Maksimal {pitch_percent}% durasi clip berisi pitch produk. Sisanya harus berupa
  cerita, kejadian, atau obrolan yang berdiri sendiri sebagai tontonan — orang
  yang tidak peduli produkmu pun harus tetap ingin menontonnya sampai habis.
- {clean_seconds} detik pertama HARAM berisi pitch, sebutan harga, atau ajakan
  membeli. Penonton yang mencium jualan di detik-detik itu pergi sebelum sempat
  tertahan.
- Pilih momen produk yang berupa REAKSI atau PEMAKAIAN, bukan penjelasan fitur.
  Orang kaget mencoba sesuatu jauh lebih kuat daripada orang membacakan
  keunggulan.
- `title` dan `description` DILARANG berbunyi seperti iklan. Tidak ada harga,
  tidak ada "promo", tidak ada huruf kapital semua. Jual ketegangan ceritanya;
  produknya biarkan penonton temukan sendiri di dalam clip.
- Kalau sebuah momen produk hanya bisa dipakai dengan cara yang terasa iklan,
  buang momen itu. Lebih baik mengembalikan sedikit clip daripada clip yang
  di-skip orang.
"""

RESPONSE_SCHEMA = {
    "type": "ARRAY",
    "items": {
        "type": "OBJECT",
        "properties": {
            "parts": {
                "type": "ARRAY",
                "items": {
                    "type": "OBJECT",
                    "properties": {
                        "start": {"type": "STRING"},
                        "end": {"type": "STRING"},
                    },
                    "required": ["start", "end"],
                },
            },
            "duration_seconds": {"type": "INTEGER"},
            "hook": {"type": "STRING"},
            "hook_start": {"type": "STRING"},
            "hook_end": {"type": "STRING"},
            "hook_placement": {
                "type": "STRING",
                "enum": ["sudah_di_awal", "pindahkan_ke_awal"],
            },
            "thumbnail_text": {"type": "STRING"},
            "thumbnail_prompt": {"type": "STRING"},
            "clip_type": {"type": "STRING", "enum": CLIP_TYPES},
            "reason": {"type": "STRING"},
            "title": {"type": "STRING"},
            "description": {"type": "STRING"},
            "hashtags": {"type": "ARRAY", "items": {"type": "STRING"}},
            "people": {"type": "ARRAY", "items": {"type": "STRING"}},
            "signal_notes": {
                "type": "OBJECT",
                "properties": {
                    "comment": {"type": "STRING"},
                    "audio": {"type": "STRING"},
                    "narrative": {"type": "STRING"},
                    "text": {"type": "STRING"},
                },
                "required": ["comment", "audio", "narrative", "text"],
            },
            "standalone_context": {"type": "NUMBER"},
            "hook_strength": {"type": "NUMBER"},
            "payoff_strength": {"type": "NUMBER"},
            "shareability": {"type": "NUMBER"},
            "discussion_trigger": {"type": "NUMBER"},
            "final_score": {"type": "NUMBER"},
        },
        "required": [
            "parts", "duration_seconds", "hook", "clip_type",
            "hook_start", "hook_end", "hook_placement",
            "thumbnail_text", "thumbnail_prompt",
            "reason", "title", "description", "hashtags", "people",
            "signal_notes",
            "standalone_context", "hook_strength", "payoff_strength",
            "shareability", "discussion_trigger", "final_score",
        ],
    },
}


def build_system_prompt(mode: str = MODE_REGULAR) -> str:
    """SYSTEM_PROMPT dasar plus blok misi sesuai mode."""
    if mode not in CAMPAIGN_MODES:
        return SYSTEM_PROMPT

    prompt = SYSTEM_PROMPT + CAMPAIGN_SYSTEM_BLOCK
    if mode == MODE_SOFTSELL:
        prompt += SOFTSELL_SYSTEM_BLOCK.format(
            pitch_percent=int(SOFTSELL_MAX_PITCH_RATIO * 100),
            clean_seconds=SOFTSELL_CLEAN_OPENING_SECONDS,
        )
    return prompt


def build_schema(mode: str = MODE_REGULAR) -> dict:
    """Schema dasar, ditambah dua field yang hanya berarti di mode campaign.

    Dibangun per-mode, bukan satu schema berisi semua field opsional: field yang
    diminta tapi tidak relevan tetap akan diisi model dengan sesuatu, dan
    "sesuatu" itu jadi angka palsu yang ikut tersimpan ke database.
    """
    if mode not in CAMPAIGN_MODES:
        return RESPONSE_SCHEMA

    schema = copy.deepcopy(RESPONSE_SCHEMA)
    schema["items"]["properties"]["campaign_fit"] = {"type": "NUMBER"}
    schema["items"]["properties"]["product_moment"] = {"type": "STRING"}
    schema["items"]["required"] += ["campaign_fit", "product_moment"]
    return schema


def build_campaign_block(
    mode: str, product: str, benefit: str = "", cta: str = ""
) -> str:
    """Detail campaign untuk pesan user — isinya berubah tiap analisis."""
    if mode not in CAMPAIGN_MODES or not product:
        return ""

    lines = [
        "=== DETAIL CAMPAIGN ===",
        f"Produk/brand yang dicari: {product}",
    ]
    if benefit:
        lines.append(f"Nilai jual yang ingin disampaikan: {benefit}")
    if cta:
        lines.append(
            f"Ajakan yang harus muncul di description: {cta}"
            + (
                " — tulis dengan santai di kalimat terakhir, jangan seperti iklan."
                if mode == MODE_SOFTSELL
                else ""
            )
        )
    lines.append(
        "Kirim HANYA kandidat yang benar-benar memuat produk ini. Kalau di "
        "seluruh kandidat di bawah tidak ada satu pun yang memuatnya, kembalikan "
        "daftar kosong — itu jawaban yang benar, bukan kegagalan."
    )
    return "\n".join(lines)


def _format_segments(segments: list[dict]) -> str:
    """Tiap baris diawali timestamp presisi milidetik.
    
    Format ini krusial agar Gemini menyalin timestamp presisi tinggi
    yang cocok dengan word-level timestamp dari Whisper.
    """
    if not segments:
        return "(tidak ada)"
    return "\n".join(
        f"[{seconds_to_timestamp(s['start'], include_ms=True)}] {s['text']}" 
        for s in segments
    )


def _overlapping(start: float, end: float, segments: list[dict]) -> list[dict]:
    """Kembalikan segmen yang overlap dengan window [start, end]."""
    return [s for s in segments if s.get("end", 0) > start and s.get("start", 0) < end]


def _format_visual_signals(window: dict, signals: dict) -> str:
    """Blok SINYAL VISUAL dari face/expression detection."""
    face_segs = signals.get("face", [])
    ws, we = window["start_seconds"], window["end_seconds"]
    matches = _overlapping(ws, we, face_segs)
    if not matches:
        return ""

    max_faces = max(s.get("face_count", 1) for s in matches)
    lines = []
    for s in matches:
        expr = s.get("dominant_expression", "")
        conf = s.get("confidence", 0)
        if expr and conf >= 0.65:
            lines.append(
                f"  {seconds_to_timestamp(s['start'])}-{seconds_to_timestamp(s['end'])}"
                f" → {expr} (conf:{conf:.0%})"
            )

    block = f"\nSINYAL VISUAL: {max_faces} orang terdeteksi"
    if lines:
        block += "\n" + "\n".join(lines)
    return block


def _format_audio_emotion_signals(window: dict, signals: dict) -> str:
    """Blok SINYAL AUDIO EMOSI dari audio emotion detection."""
    audio_segs = signals.get("audio_emotions", [])
    ws, we = window["start_seconds"], window["end_seconds"]
    matches = _overlapping(ws, we, audio_segs)
    # Saring silence dan neutral agar tidak berisik — kecuali semua memang neutral
    notable = [s for s in matches if s.get("emotion") not in ("silence", "neutral")]
    segs_to_show = notable if notable else matches[:2]
    if not segs_to_show:
        return ""

    lines = []
    peak_energy = max((s.get("avg_energy", 0) for s in segs_to_show), default=0)
    for s in segs_to_show:
        emotion  = s.get("emotion", "")
        energy   = s.get("avg_energy", 0)
        intensity = s.get("intensity", 0)
        peak_mark = " ← PEAK" if energy == peak_energy and energy > 0.6 else ""
        lines.append(
            f"  {seconds_to_timestamp(s['start'])}-{seconds_to_timestamp(s['end'])}"
            f" → {emotion}  energy={energy:.2f}  intensity={intensity:.2f}{peak_mark}"
        )

    return "\nSINYAL AUDIO EMOSI:\n" + "\n".join(lines)


def _format_candidate(index: int, window: dict, context: dict, signals: dict | None = None) -> str:
    comment = window.get("score_comment")
    comment_str = "n/a" if comment is None else f"{comment:.2f}"
    peak = (window.get("audio_features") or {}).get("peak_position")
    peak_str = f" (puncak di {peak:.0%})" if peak is not None else ""

    signals = signals or {}
    visual_block = _format_visual_signals(window, signals)
    audio_block  = _format_audio_emotion_signals(window, signals)

    return (
        f"[KANDIDAT {index}]  Inti: {seconds_to_timestamp(window['start_seconds'])}"
        f" → {seconds_to_timestamp(window['end_seconds'])}"
        f"  |  Prefilter score: {window.get('score_final', 0):.2f}\n"
        f"Sinyal: comment={comment_str}"
        f" audio={window.get('score_audio', 0):.2f}{peak_str}"
        f" narrative={window.get('score_narrative', 0):.2f}"
        f" text={window.get('score_text', 0):.2f}"
        f"{visual_block}"
        f"{audio_block}\n"
        f"\n--- KONTEKS SEBELUM ---\n{_format_segments(context['before'])}"
        f"\n--- INTI ---\n{_format_segments(context['core'])}"
        f"\n--- KONTEKS SESUDAH ---\n{_format_segments(context['after'])}"
    )


def build_prompt(
    windows: list[dict],
    whisper_segments: list[dict],
    video_title: str,
    channel: str,
    language: str | None = None,
    min_seconds: int = MIN_CLIP_SECONDS,
    max_seconds: int = MAX_CLIP_SECONDS,
    performance_hint: str | None = None,
    campaign_block: str = "",
    signals: dict | None = None,
) -> str:
    signals = signals or {}
    blocks = [
        _format_candidate(
            i,
            window,
            get_context_segments(window, whisper_segments, CONTEXT_SECONDS),
            signals,
        )
        for i, window in enumerate(windows, 1)
    ]

    lang_line = (
        f"Bahasa transcript: {language}. Tulis title, description, dan hashtag "
        f"dalam bahasa ini.\n"
        if language
        else ""
    )

    # Rentang durasi diulang di sini, bukan cuma di system prompt, karena user
    # mengubahnya per-analisis. Kalimat "kalau ragu pilih yang lebih pendek"
    # penting: tanpa arah, model mengendap di tengah-atas rentang berapa pun
    # yang diberikan — pada rentang 30-90 hasilnya bermedian 63 detik.
    duration_line = (
        f"Durasi tiap clip WAJIB antara {min_seconds} dan {max_seconds} detik "
        f"(total semua bagian). Kalau dua titik potong sama kuatnya, pilih yang "
        f"lebih pendek — panjang hanya kalau gagasannya memang menuntut.\n"
    )

    # Diletakkan SETELAH aturan durasi tapi SEBELUM daftar kandidat, supaya
    # terbaca sebagai konteks penilaian — bukan sebagai aturan keras yang
    # menyaingi rentang durasi di atasnya.
    hint_block = f"\n{performance_hint}\n" if performance_hint else ""

    # Detail campaign ditaruh PALING ATAS, sebelum aturan durasi maupun data
    # performa: ia menentukan kandidat mana yang layak dilihat sama sekali,
    # sementara sisanya baru menentukan bagaimana yang layak itu dipotong.
    campaign = f"\n{campaign_block}\n" if campaign_block else ""

    return (
        f"Video: {video_title}\n"
        f"Channel: {channel}\n"
        f"{lang_line}"
        f"{campaign}"
        f"{duration_line}"
        f"{hint_block}\n"
        f"Berikut {len(windows)} kandidat hasil prefilter, diurutkan dari skor "
        f"tertinggi.\n"
        f"Pilih maksimal {FINAL_CANDIDATES} yang paling layak jadi short.\n\n"
        + "\n\n".join(blocks)
    )


def analyze(
    windows: list[dict],
    whisper_segments: list[dict],
    video_title: str,
    channel: str,
    language: str | None = None,
    api_key: str | None = None,
    min_seconds: int = MIN_CLIP_SECONDS,
    max_seconds: int = MAX_CLIP_SECONDS,
    performance_hint: str | None = None,
    mode: str = MODE_REGULAR,
    campaign: dict | None = None,
    signals: dict | None = None,
) -> list[dict]:
    """Kirim kandidat ke Gemini, kembalikan clip pilihannya (belum divalidasi).

    `min_seconds`/`max_seconds` datang dari slider di UI. Rentang yang sama
    WAJIB dioper ke `validator.validate()` — kalau keduanya beda, Gemini menulis
    clip dengan satu aturan lalu dibuang oleh aturan lain. Hal yang sama berlaku
    untuk `mode`: validator memakainya untuk menyaring kecocokan campaign.
    """
    if not windows:
        return []

    campaign = campaign or {}
    campaign_block = build_campaign_block(
        mode,
        campaign.get("product", ""),
        campaign.get("benefit", ""),
        campaign.get("cta", ""),
    )

    prompt = build_prompt(
        windows, whisper_segments, video_title, channel, language, min_seconds, max_seconds,
        performance_hint, campaign_block, signals=signals,
    )

    config = types.GenerateContentConfig(
        system_instruction=build_system_prompt(mode),
        response_mime_type="application/json",
        response_schema=build_schema(mode),
        max_output_tokens=GEMINI_MAX_OUTPUT_TOKENS,
    )

    # Retry dan pindah model ditangani core/gemini_client: error jaringan
    # diulang di model yang sama, kuota habis langsung pindah model berikutnya.
    response, model_used = gemini_client.generate(prompt, config, api_key)
    _raise_if_truncated(response)

    if model_used != GEMINI_MODEL:
        logger.warning("Analisis clip dikerjakan %s, bukan %s", model_used, GEMINI_MODEL)

    return json.loads(response.text)
