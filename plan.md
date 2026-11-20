# Rencana: Editor Otomatis Berbasis Gemini

Tujuan: Gemini bertindak sebagai editor. Ia memilih sendiri tool apa yang dipakai,
kapan, dan dengan parameter apa, mengikuti **persona** channel dan **isi** klip
(lucu, motivasi, debat, dan seterusnya). Kode kita menyediakan tool, data, dan
pagar pengaman. Gemini tidak pernah menulis perintah FFmpeg.

---

## 1. Kondisi sekarang dan masalahnya

| Area | Kondisi | Masalah |
|---|---|---|
| Gemini edit (`/editor/auto-config`) | Memilih 1 preset subtitle, 1 filter, 0–3 SFX, 0–2 teks | Hanya melihat judul/hook/skor. Tidak melihat transkrip, shot, wajah, pembicara, emosi audio. Output JSON tidak divalidasi skema. |
| Render (`backend/renderer/core.py`) | Urutan tetap: extract → subtitle → filter/teks → SFX | Encode ulang sampai 3×. Grid clip pipeline terpisah, tidak bisa digabung. |
| Subtitle | 16 animasi, 6 font, posisi atas/tengah/bawah | Timing kata ditebak dari jumlah huruf (`_estimate_word_times`), padahal Whisper sudah memberi timestamp per kata (`core/transcriber.py`). Kata highlight dipilih aturan kaku. Posisi tetap, bisa menutupi wajah. |
| Teks overlay | drawtext | Ukuran 48 tetap, 3 posisi, kotak tetap. |
| Hook | `hook_start/hook_end/hook_placement` dihasilkan Gemini | Tidak dipakai saat render. |
| SFX | 8 file, kategori kasar | Tidak ada tag mood/intensitas. Timing dalam detik mentah, tidak terikat kata. |
| Musik latar | Tidak ada | — |
| Orang di video | `people` = nama dari Gemini; grid tahu wajah per shot | Nama tidak terhubung ke wajah. Pelacak salah hitung orang lintas kamera (5 orang terbaca 3). |

Catatan dari diagnosis channel (`docs/CLIPODIFY_DIAGNOSIS.md`): retensi sudah sehat,
yang mati adalah **share dan komentar**. Jadi target utama editing bukan sekadar
"rapi", tapi memancing reaksi. Ini jadi salah satu sasaran persona (bagian 4).

---

## 2. Arsitektur target

```
                 ┌──────────────────── Context Pack ─────────────────────┐
 transkrip+kata ─┤ beats? (belum)  shots+grid+zona wajah  pembicara/shot │
 emosi audio  ───┤ ekspresi wajah  identitas orang  aset tersedia        │
                 └───────────────────────────┬───────────────────────────┘
                                             ▼
 Persona (YAML) ──►  Gemini tahap 1: ANALISIS BEAT  ──►  daftar beat + intent
 Playbook (YAML) ─►  Gemini tahap 2: RENCANA EDIT   ──►  EditPlan (JSON, response_schema)
                                             ▼
                                  Validator / Normalizer
                       (snap ke kata, hindari wajah, batas jumlah, dll)
                                             ▼
                              Renderer satu-pass (FFmpeg)
                                             ▼
                               video final + plan tersimpan
```

Prinsip:
1. **EditPlan adalah satu-satunya bahasa antara Gemini dan renderer.** Tool baru =
   jenis operasi baru di skema + kompiler kecil di renderer.
2. **Semua waktu ditambatkan ke peristiwa, bukan detik mentah.** Gemini menulis
   "setelah kata ke-42" atau "di beat punchline #3"; validator yang mengubahnya ke
   frame. Ini kunci supaya SFX dan zoom jatuh tepat.
3. **Gemini kreatif, validator tegas.** Apa pun output Gemini, hasil render tidak
   boleh rusak.
4. **Selalu simpan plan + versi persona + metrik video** supaya nanti bisa belajar
   pilihan edit mana yang menaikkan share/komentar.

---

## 3. EditPlan (skema)

Kerangka (disederhanakan, versi final dikunci di `response_schema`):

```json
{
  "persona_id": "stefra_default",
  "intent": "funny",
  "reasoning": "…",
  "timeline": [
    {"op": "keep",    "from_word": 0,  "to_word": 118},
    {"op": "cut",     "kind": "silence|filler", "auto": true},
    {"op": "move_hook_to_start", "from_word": 64, "to_word": 80}
  ],
  "framing": {"mode": "grid_auto|single_speaker|track_crop"},
  "zoom":    [{"at_beat": 3, "scale": 1.15, "duration_s": 0.8, "ease": "punch"}],
  "subtitle": {
    "font": "anton", "animation": "word_pop", "size": 1.0,
    "color": "#FFFFFF", "highlight_color": "#FFE000",
    "highlight_words": [12, 13, 47], "emoji": [{"after_word": 47, "emoji": "😂"}],
    "placement": "auto_avoid_faces"
  },
  "text":   [{"text": "…", "role": "hook_title|question|cta", "at_beat": 0, "style": "…"}],
  "sfx":    [{"tag": "pop", "anchor": {"word": 47, "edge": "end"}, "offset_s": 0.05, "gain_db": -6}],
  "music":  {"mood": "playful", "energy": "mid", "duck_db": -14, "fade_in_s": 0.5, "fade_out_s": 1.0},
  "grade":  {"preset": "warm", "contrast": 1.1, "saturation": 1.15},
  "end_card": {"kind": "comment_prompt", "text": "…"}
}
```

Operasi tahap awal (semua pakai kode yang sudah ada kecuali ditandai **baru**):

| op | Sumber | Status |
|---|---|---|
| `keep` / `cut` (silence, filler) | transkrip per kata | **baru** |
| `move_hook_to_start` | field hook dari Gemini | **baru** (data sudah ada) |
| `framing` | `grid_composer.py`, `tracker.py` | ada, perlu dibungkus |
| `zoom` | crop + scale berwaktu | **baru** |
| `subtitle` | `renderer/subtitle.py` | ada, perlu timestamp asli + highlight dari plan + posisi otomatis |
| `text` | drawtext → pindah ke ASS | ada, perlu parameter lebih |
| `sfx` | `renderer/sfx.py` | ada, perlu tag + anchor |
| `music` | — | **baru** |
| `grade` | `renderer/filters.py` | ada, buka parameter |
| `end_card` | — | **baru** |

---

## 4. Persona

**Apa itu:** file konfigurasi per channel yang mendefinisikan *rasa* editing.
Gemini membacanya sebagai panduan gaya; validator membaca bagian `limits` sebagai
aturan keras.

Contoh `personas/stefra_default.yaml`:

```yaml
id: stefra_default
name: "Stefra — santai, receh, hangat"
audience: "18–30, Indonesia, nonton sambil scroll"
voice: "bahasa gaul ringan, tidak menggurui, tidak lebay"
goal: "penonton tag teman / komentar pendapat"   # dari diagnosis: share & komen
pace: fast            # slow | medium | fast → kerapatan cut
subtitle_family: [word_pop, scale_highlight, karaoke_bold]
fonts: [anton, montserrat_xbold]
palette: {text: "#FFFFFF", highlight: "#FFE000", accent: "#FF3B6B"}
emoji: sparing        # none | sparing | heavy
sfx_density: medium   # none | low | medium | high
music: {allowed: true, default_mood: playful, max_level_db: -20}
grade_family: [warm, viral]
always:
  - "akhiri dengan pertanyaan yang memancing komentar"
never:
  - "SFX di atas kalimat serius / sedih"
  - "teks menutupi wajah"
limits:               # dipaksa validator
  max_sfx_per_10s: 2
  max_zoom_per_10s: 1
  min_gap_between_sfx_s: 1.5
examples:             # opsional: 2–3 plan bagus dari video yang performanya tinggi
  - plans/examples/funny_001.json
```

**Cara kerja:**
1. Persona dipilih per job (default per channel di settings).
2. Seluruh isi persona disisipkan ke system prompt tahap 2 sebagai "panduan gaya".
3. `limits` dipakai validator, jadi persona bisa membatasi Gemini secara pasti.
4. `examples` (few-shot) adalah cara paling efektif mengunci *vibe*: Gemini meniru
   plan nyata yang sudah terbukti bagus.
5. Tahap lanjut: persona diperbarui dari data (bagian 9).

---

## 5. Gemini paham isi klip: analisis beat

Satu label per klip (`clip_type`) tidak cukup. Klip lucu punya setup, punchline,
dan tawa; klip motivasi punya pembangunan, kalimat kunci, dan penutup. Edit yang
tepat bergantung pada **momen**, bukan hanya genre.

**Tahap 1 — Beat analysis (Gemini).** Input: transkrip per kata dengan nomor kata,
pembicara per shot, emosi audio per segmen (laughing/excited/…), ekspresi wajah,
batas shot. Opsional: video klip itu sendiri (Gemini menerima video, jadi ia bisa
"menonton" tawa dan ekspresi; biaya token perlu diukur dulu).

Output:
```json
{
  "intent": "funny",
  "secondary_intent": "story",
  "beats": [
    {"id": 0, "type": "hook",      "from_word": 0,  "to_word": 9},
    {"id": 1, "type": "setup",     "from_word": 10, "to_word": 40},
    {"id": 2, "type": "punchline", "from_word": 41, "to_word": 47, "energy": 0.9},
    {"id": 3, "type": "laugh",     "t_from": 21.3, "t_to": 23.0, "who": "B"},
    {"id": 4, "type": "key_line",  "from_word": 90, "to_word": 104}
  ]
}
```

Jenis beat awal: `hook, setup, punchline, laugh, reaction, build, key_line,
emotional_peak, twist, question, payoff, filler`.

**Tahap 2 — Rencana edit (Gemini).** Input: beat + persona + **playbook** + aset.
Output: EditPlan.

### Playbook: intent × beat → resep edit

Disimpan sebagai data (`playbook.yaml`), bukan ditanam di kode, supaya bisa diubah
tanpa coding. Persona boleh menimpa.

| Intent | Teks / subtitle | SFX | Musik | Zoom / visual |
|---|---|---|---|---|
| **funny** | Animasi energik (word_pop/bounce), highlight kata lucu, emoji 😂 setelah punchline | `pop`/`boing` tepat di akhir kata punchline; tawa → tanpa SFX (tawa asli sudah cukup) | playful, energi sedang, ducking dalam | Punch-in 1.15× di punchline; potong ke wajah yang tertawa |
| **motivasi / insight** | Karaoke/scale_highlight, kalimat kunci besar di tengah, tanpa emoji | Minim; `rise` sebelum key_line, `impact` lembut di kata terakhirnya | inspirational/cinematic, naik menjelang key_line | Zoom pelan (1.0→1.08) selama build; grade cinematic |
| **debate / rant** | Tegas, warna aksen merah, highlight kata keras | `impact`/`whoosh` di pergantian pembicara, jarang | tense, rendah, atau tanpa musik | Grid bergantian; punch-in saat nada naik |
| **story** | Sentence-level, rapi | Hampir tidak ada; `whoosh` di transisi bab | ambient lembut | Minim |
| **reveal** | Teks "tunggu…" sebelum reveal | `rise` → `impact` tepat di kata reveal | build lalu drop (musik berhenti 0.5s sebelum reveal) | Zoom di reveal |
| **sedih / emosional** | Animasi halus (fade), tanpa emoji | Tidak ada | piano/ambient, sangat pelan | Grade moody, tanpa zoom cepat |

Aturan umum:
- Tidak ada SFX di atas suara orang yang sedang bicara (validator cek dengan RMS).
- SFX selalu ditambatkan ke `word.end` atau awal/akhir beat, lalu `offset_s` kecil.
- Satu klip boleh punya lebih dari satu intent; resep dipilih per beat.

---

## 6. Teks, SFX, dan musik: detail implementasi

### Subtitle dan teks
1. Pakai timestamp kata asli dari Whisper (sudah ada di `transcriber.py`).
2. Gemini memilih `highlight_words` (indeks kata) dan emoji, menggantikan
   `_is_key_word`.
3. Posisi otomatis menghindari wajah: grid composer mengekspor zona wajah per shot
   di koordinat output 1080×1920; subtitle diberi `\pos(x,y)` per baris. Posisi
   dipertahankan selama masih aman, pindah hanya kalau bertabrakan.
4. Teks overlay pindah dari `drawtext` ke ASS supaya ukuran, warna, outline,
   animasi, dan posisi bisa diatur dari plan.

### SFX
1. Perluas library + metadata per file: `tag` (pop, boing, whoosh, impact, rise,
   ding, record_scratch, crowd_laugh, …), `mood`, `intensity`, `duration`.
2. Gemini memilih **tag**, bukan nama file. Kode memilih file yang cocok
   (bisa diacak supaya tidak monoton).
3. Validator: snap ke kata, jarak minimum, maks per 10 detik, tidak di atas
   ucapan, gain dibatasi.

### Musik latar
1. Library musik + metadata (`mood`, `energy`, `bpm`, durasi, **lisensi**).
   Metadata bisa diisi otomatis dengan librosa (bpm/energi) + label mood manual.
2. Gemini memilih `mood` + `energy`, dan opsional kurva (naik di key_line,
   berhenti sebelum reveal).
3. Renderer: potong/loop ke durasi, fade in/out, **ducking** dengan
   `sidechaincompress` di bawah suara, lalu `loudnorm` ke target platform
   (sekitar −14 LUFS).
4. ⚠ Wajib musik bebas royalti / berlisensi jelas. Musik berhak cipta bisa
   kena klaim dan memblokir monetisasi.

---

## 7. Siapa orang di video, dan thumbnail

### Mengenali orang (identitas lintas shot)
1. **Clustering wajah:** aktifkan modul *recognition* InsightFace (model `buffalo_sc`
   sudah mengandungnya; sekarang hanya `detection` yang dipakai). Ambil embedding
   setiap wajah per shot, kelompokkan jadi identitas (A, B, C, …). Ini sekaligus
   memperbaiki masalah pelacak yang menghitung 5 orang sebagai 3.
2. **Memberi nama tiap cluster** dari konteks, bukan dari pengenalan wajah selebriti:
   - nama tamu di judul/deskripsi episode,
   - nama yang disebut di transkrip ("iya Mah…", "kata Bang …"),
   - siapa yang bicara saat nama itu disapa (pembicara per shot dari gerak bibir).
   Gemini merangkum bukti ini → `{A: "Mamah Dedeh", B: "host", C: "unknown"}`
   dengan tingkat keyakinan. Kalau ragu, pakai label peran (host/tamu), bukan nama.
3. Hasilnya dipakai di semua tempat: grid (cell utama), subtitle (warna per orang,
   opsional), thumbnail, dan field `people` yang sekarang hanya teks.

### Thumbnail
1. Tentukan **orang utama** klip: pembicara dominan atau orang yang jadi pokok bahasan
   (`people` + cluster).
2. Kumpulkan kandidat frame **dari cluster orang itu saja**, lalu beri skor:
   ekspresi kuat (DeepFace / emosi audio / tawa), mata terbuka, wajah tajam, ukuran
   wajah besar, tidak tertutup tangan/mic. Pemberi skor ini sudah sebagian ada di
   `backend/analyzer/thumbnail.py`.
3. Kirim 6–10 kandidat teratas (gambar kecil) ke Gemini bersama hook dan
   `thumbnail_text`. Gemini memilih 1–3 yang paling cocok dengan cerita klip dan
   menulis prompt AI-gambar seperti sekarang.
4. Catatan jujur: menurut data diagnosis, sebagian besar view Shorts datang dari
   feed (thumbnail hampir tidak tampil di sana). Thumbnail berpengaruh di search,
   halaman channel, dan video panjang. Prioritasnya di bawah editing yang memancing
   share/komentar.

---

## 8. Validator / normalizer

Berjalan setelah Gemini, sebelum render. Semua pelanggaran dikoreksi otomatis dan
dicatat (bukan dibuang diam-diam):
- indeks kata/beat valid; waktu dikonversi ke frame
- SFX/zoom memenuhi `limits` persona; SFX tidak di atas ucapan
- teks dan subtitle tidak bertabrakan dengan zona wajah
- cut tidak memotong di tengah kata; hook yang dipindah tidak merusak kalimat
- aset yang dirujuk benar-benar ada
- total durasi tetap di rentang target

---

## 9. Belajar dari performa (tahap lanjut)

Simpan per video yang diupload: EditPlan, versi persona, intent, dan metrik
(view, share, komentar, retensi dari `meta_analytics` / `youtube_analytics`).
Setelah cukup data:
- bandingkan pilihan edit vs share/komentar (misal: end card pertanyaan vs tidak),
- pilih plan terbaik jadi `examples` persona,
- usulkan perubahan persona ke user (bukan otomatis diubah).

---

## 10. Tahapan kerja

**Fase 1: Fondasi (tanpa tool baru)** ✅ selesai 2026-11-19 — kode di `backend/editing/`
- [x] Skema EditPlan v1 + `response_schema` (`plan_schema.py`)
- [x] Context pack: kata ber-timestamp, shot + grid, emosi audio (`context.py`, cache `tmp/edit/{id}/`).
      Zona wajah di koordinat output belum — pindah ke Fase 2.
- [x] Renderer satu-pass: framing, grade, efek berwaktu, subtitle (dipecah ≤4 kata pakai waktu kata asli), teks, SFX (`renderer.py`)
- [x] Validator dasar + log koreksi (`validator.py`); plan + koreksi disimpan `tmp/edit/{id}/plan_*.json`
- [x] Endpoint `/api/editor/auto-plan` + tombol "Auto Edit" di Editor; `/editor/auto-config` lama dibiarkan
- Catatan uji (klip 258, 78 dtk): konteks 37 dtk (sekali, lalu cache), Gemini 1 panggilan, render ~30 dtk.
  Saat model utama 503, klien jatuh ke flash-lite — kualitas teks pertanyaan cenderung generik.
- Selesai bila: satu klip dirender dari plan Gemini dalam 1 encode, hasil setara atau lebih baik dari alur lama.

**Fase 2: Subtitle yang benar** ✅ selesai 2026-11-19
- [x] Timestamp kata asli dipakai builder animasi (`subtitle.py`: `_REAL_WORDS`); baris subtitle dipotong
      ≤4 kata dan ≤N karakter supaya tidak terlipat
- [x] Highlight kata dari plan (`subtitle.highlight_words`, maks 1 per 8 kata) — warna untuk animasi
      per kata/kalimat, membesar untuk karaoke
- [ ] Emoji di subtitle — ditunda: libass tidak bisa merender emoji berwarna
- [x] Posisi otomatis menghindari wajah: `grid_composer.output_face_zones` (wajah di kanvas output,
      crop identik dengan render) + `editing/placement.py` (posisi lama dipertahankan selama aman,
      lalu sambungan antar cell, lalu cari terdekat)
- [x] Teks overlay lewat ASS (kotak latar, pop-in, menghindari wajah dan subtitle); drawtext dihapus

**Fase 3: Persona + beat** ✅ selesai 2026-11-19
- [x] Persona YAML `personas/stefra_default.yaml` (DRAF — isi disesuaikan pemilik channel) + loader
      `backend/editing/persona.py`; `limits` dipaksakan validator; persona id + versi (hash) disimpan di setiap plan.
      Pemilih persona di UI ditunda sampai ada >1 persona (sekarang via env `EDIT_PERSONA`).
- [x] Beat analysis — **digabung dalam SATU panggilan Gemini** (kuota gratis): `beats` adalah field pertama
      skema (`property_ordering`), jadi Gemini memetakan momen dulu baru memilih edit.
- [x] `personas/playbook.yaml` (resep intent × beat) masuk ke system prompt.
- [x] Aturan berbasis beat di validator: SFX di beat `laugh`/`emotional_peak` dibuang (`no_sfx_in_beats`).
- [ ] Few-shot contoh plan — mekanisme siap (`examples:` di persona), daftar masih kosong sampai ada plan
      yang terbukti bagus dari data performa (Fase 6). Sengaja tidak diisi contoh karangan.
- Catatan uji (klip 258): 6 beat masuk akal, SFX/efek jatuh di beat yang tepat, 0 koreksi validator.
  Waktu Gemini ~3 menit dengan gemini-3.6-flash (prompt lebih panjang + thinking) — perlu dipantau.

**Fase 4: Tool baru** ✅ selesai 2026-11-19 — inti: `backend/editing/timeline.py` (waktu klip asli → waktu hasil)
- [x] Potong jeda (>0.45 dtk, sisa napas 0.12 dtk) + kata pengisi + potongan pilihan Gemini; beat
      punchline/key_line/twist/payoff/laugh dilindungi; total potongan maks `max_cut_ratio` persona (30%).
      Sambungan audio diberi fade 8 ms supaya tidak "klik". Catatan: Whisper jarang menulis "eee/ehm", jadi
      potong pengisi lewat transkrip efeknya kecil.
- [x] Hook teaser (flash-forward 1.2–7 dtk di detik 0, aslinya tetap di tempat) + SFX transisi otomatis.
      `hook_placement` dari analisis awal tetap dihormati (klip yang hook-nya sudah di awal tidak di-teaser).
- [x] Zoom punch/slow (zoompan ke arah wajah, hanya shot 1 orang; jatah 1/10 dtk dari persona).
- [x] SFX bertag: Gemini memilih tag, kode memilih file (`assets/sfx/sfx_meta.yaml`); +5 SFX sintetis
      bebas hak cipta (`tools/make_sfx.py`): boing, bass_drop, chime, tick, swipe.
- [x] Musik latar: library + metadata lisensi WAJIB (`assets/music/music_meta.yaml`, `tools/index_music.py`),
      ducking `sidechaincompress` oleh suara, fade in/out, kredit atribusi tampil di UI bila diwajibkan.
      **Library masih kosong** — pipeline diuji dengan trek sintetis (level −17 dB saat bicara).
- [x] Loudness akhir `loudnorm` −14 LUFS (semua render auto-plan).
- [x] End card: frame terakhir dibekukan + digelapkan + teks pertanyaan; `question` ganda di akhir dibuang.
- [x] Pertanyaan generik ("setuju gak", "gimana menurut kalian") dicatat di log validator sebagai pelanggaran persona.

**Fase 5: Identitas orang + thumbnail** ✅ selesai 2026-11-19
- [x] Clustering wajah: `face_detector.face_embedding()` (ArcFace w600k_mbf dari buffalo_sc, wajah disejajarkan
      5 titik) + `context._identities()` (threshold cos 0.38; dua wajah di shot yang sama tidak boleh satu orang).
      Uji klip 258: tepat 5 orang konsisten di 12 shot (orang sama 0.41–0.92, beda ≤0.30). Hasil: `ctx["people"]`
      (lama tampil, lama bicara dari skor gerak bibir per shot, contoh kalimat) + `shots[k]["identity"]`.
- [x] Penamaan dari BUKTI (judul, sapaan di transkrip, siapa bicara di shot mana) — field `people` di skema
      plan, dalam panggilan Gemini yang SAMA (tanpa kuota tambahan). Uji: ID1 "Mamah Dedeh" (0.95), ID2 "Ipeh"
      (0.85, "disapa 'Ipe' di kata #0"), sisanya label peran dengan keyakinan rendah. Nama dipakai bila ≥0.5.
- [x] Thumbnail orang utama (`editing/thumbnails.py`): frame dari shot orang yang paling banyak bicara
      (close-up dulu), skor tajam/mata/mulut/frontal/ukuran/emosi, output potret 1080×1920. Endpoint lama
      `/api/analyzer/thumbnail` untuk kandidat klip kini memakai jalur ini (UI tetap); ~18 dtk per klip.
- [ ] Pemilihan thumbnail oleh Gemini (lihat gambar) — ditunda: butuh panggilan + input gambar, kuota gratis.

**Fase 6: Belajar dari performa** ✅ selesai 2026-11-19 (mesin siap; menunggu data)
- [x] Tabel `edit_renders`: setiap render Auto Edit dicatat otomatis (persona + versi, model, intent, fitur edit
      ringkas dari `learning.plan_features`); plan lama di `tmp/edit/*/plan_*.json` di-backfill.
- [x] Hubungkan render ↔ video YouTube (upload masih manual): saran otomatis = tayang setelah render + judul
      paling mirip; user konfirmasi lewat dropdown di halaman **Learning** (`/learning`).
- [x] Laporan: fitur edit (intent, animasi, grade, pertanyaan/end card, teaser, SFX, zoom, potongan, musik,
      pertanyaan generik) vs share rate, komentar rate, retensi. Hanya video ≥100 views; kesimpulan hanya bila
      tiap kelompok ≥3 video DAN selisih ≥1.5× (diuji dengan data buatan di memori).
- [x] Usulan persona: 3 plan dengan reaksi tertinggi → tombol "Jadikan contoh persona" menulis `examples:` di
      YAML (komentar file tetap utuh). Persona tidak pernah diubah tanpa klik user.
- Catatan: data share/komentar di `yt_videos` masih kosong (diambil 2026-08-17, sebelum kolomnya ada) →
  tekan "Refresh data YouTube". Metrik Meta (IG/FB) belum ikut dibandingkan.

---

**Fase 7: Studio Subtitle, font Google, analisis warna** ✅ selesai 2026-11-19
- [x] Template lama dihapus (dipindah ke Trash sistem): 90 preset subtitle (font×animasi), 6 file .ttf, folder
      preview, `renderer/subtitle.py`, `renderer/core.py`; preset filter dihapus dari `renderer/filters.py`.
      Auto Config lama dihapus; render manual Editor kini memakai mesin Auto Edit (`_manual_plan`).
- [x] Font dari Google Fonts (`editing/fonts.py`): katalog via Developer API (key di Settings → `app_settings`),
      unduh TTF on-demand ke `assets/fonts/gf/` + nama libass dibaca dari file; tanpa key → layanan CSS + 12 font dasar.
- [x] Resep gaya subtitle sebagai aset (`assets/subtitle_styles/*.json`, `editing/subtitle_style.py`): font, ukuran,
      huruf, kata per layar, warna (tunggal/berganti per kata/baris), outline, bayangan, kotak, kata aktif, highlight,
      animasi (pop/bounce/slide_up/fade/wipe), posisi. Validator menjaga keterbacaan. Gaya favorit pertama:
      "Kata Warna Bergantian" (dari 3 screenshot CapCut).
- [x] Gemini menulis resep di Auto Edit (turunan gaya favorit atau "baru"); resep baru/variasi otomatis tersimpan di
      Studio (✨ Gemini). Grade = resep (contrast/saturation/brightness/gamma/warmth/tint), bukan preset.
- [x] Halaman Studio (`/studio`): editor resep, preview 4.5 dtk dengan renderer asli (~1.5 dtk), "Buat dengan Gemini"
      dari deskripsi (1 panggilan, tidak otomatis disimpan), simpan/simpan baru/hapus (ke .trash).
- [x] Analisis warna tampilan akhir (`editing/color_analysis.py`, ~3 dtk/klip): kanvas 1080×1920 identik render;
      warna dominan area subtitle, kecerahan/kontras/saturasi/cast hangat-tint, warna & saturasi kulit wajah →
      masuk prompt Gemini. Simulasi grade di frame sampel: dikurangi bila detail highlight/bayangan hilang, saturasi
      berlebihan, kulit terlalu oranye, atau wajah terlalu terang. Cek kontras subtitle vs warna area.
- Catatan: model lite masih kadang mengabaikan saran warna (mis. tetap menambah warmth pada video yang sudah hangat);
  simulasi hanya mencegah yang MERUSAK, bukan yang sekadar kurang ideal.

**Fase 8: Editing terasa modern — dikerjakan satu langkah kecil per sesi** ⏳ belum mulai

Prinsip: tiap langkah selesai = bisa dicoba sendiri, diuji di klip 258 (+1 klip lain), user menilai hasilnya
sebelum lanjut ke langkah berikutnya. "Kalau diperlukan" = keputusan per beat oleh Gemini + rem dari validator.
Satu langkah tidak menambah panggilan Gemini kecuali disebut.

*8A. Ritme & sound design (tanpa aset baru)*
- [x] **8A-1 Peta ritme.** ✅ 2026-11-19 — `editing/rhythm.py`; hasil render punya `rhythm` (perubahan, celah ≥4 dtk,
      celah ≥6 dtk ditandai); ritme bawaan klip (cut kamera) masuk prompt Gemini; tampil di ringkasan Auto Edit;
      ikut dibandingkan di Learning. Uji 258: asli 11 perubahan/celah maks 15.8 dtk → hasil edit 19 perubahan/
      tiap 3.7 dtk, celah maks 10.0 dtk, 5 celah ≥6 dtk. Belum mengubah render.
- [x] **8A-2 Whoosh di perpindahan besar.** ✅ 2026-11-19 — `renderer.transition_whooshes`: whoosh/swipe pelan
      (volume 0.45, persona `transition_sfx`) di cut yang mengganti layout; puncak bunyi (diukur per file,
      `sfx.peak_s`) diselaraskan ke frame cut; dilewati di beat punchline/tawa/puncak emosi atau bila ≤1.5 dtk dari
      SFX lain; render manual tidak memakai. Uji 258: 6 dipasang, 2 dilewati (dekat ding/rise); semua puncak
      tepat di frame cut (0 ms, diukur korelasi gelombang). Catatan: celah ritme tidak berubah karena whoosh
      menempel di cut yang sudah ada — mengisi celah adalah tugas 8A-3/8A-4.
- [x] **8A-3 Riser → hit.** ✅ 2026-11-19 — tool plan `build_ups {to_word, length short/long, hit impact/bass_drop/none}`,
      maks 2/klip, jarak ≥8 dtk, tidak di tawa/puncak emosi; SFX lain di dekatnya dibuang. Riser baru sintetis
      (`sfx_riser_short` 1 dtk, `sfx_riser_long` 2 dtk — makin keras, berhenti tajam; `sfx_rise` lama ternyata
      memudar). Akhir riser & puncak hit dihitung di waktu HASIL (`editing/pacing.py`). Uji 258 (build-up tulisan
      tangan): keempat titik tepat di awal kata (0 ms); whoosh di dekatnya otomatis dilewati.
- [x] **8A-4 Aturan celah.** ✅ 2026-11-19 — `pacing.fill_gaps`: celah > `max_gap_s` (6) diisi zoom punch 1.08 (shot
      1 orang) atau SFX ringan tap/pop/tick (vol 0.4) di kata terkuat yang membelah celah; dilewati di tawa/puncak
      emosi dan klip intent `emotional`; jatah ±1 per 12 dtk; Gemini diberi aturan yang sama. Uji 258: celah >6 dtk
      5 → 0, celah terpanjang 10.0 → 6.0 dtk, 24 perubahan (tiap 3.0 dtk).

*8B. Library SFX lebih kaya*
- [x] **8B-1 Importer audio** ✅ 2026-11-19 — `editing/audio_library.py` + `tools/import_audio.py` + bagian "Impor Audio" di
      Assets. File → `assets/inbox/` (unggah lewat UI atau taruh manual) → isi jenis (SFX: tag/kegunaan/intensitas/riser;
      musik: judul/mood/energi) + lisensi WAJIB (YouTube Audio Library, CC0, CC BY + kredit wajib, Pixabay, buatan
      sendiri, lainnya + penjelasan); NC/ND ditolak; duplikat (hash) ditolak; puncak/BPM/durasi diukur otomatis;
      tag baru langsung tersedia untuk Gemini. Upload lama yang melewati lisensi dihapus; hapus SFX → `.trash`.
- [x] **8B-2 Kategori baru** ✅ 2026-11-19 — sintesis bebas hak cipta (`tools/make_sfx.py`): whoosh +2 (total 4 + swipe),
      hit ×2 (tag baru), boom (gelap, pusat spektrum 93 Hz), record scratch, ding game show (tag ding, total 2),
      buzzer "salah". Validator memutar varian per tag sesuai urutan muncul (tidak ada file sama berturut-turut).
      Playbook diberi panduan scratch/buzzer/ding/hit/boom. **Tawa penonton TIDAK disintesis** (akan terdengar palsu)
      — perlu rekaman asli via Impor Audio, tag `laugh`.
- [x] **8B-3 Normalisasi level SFX** ✅ 2026-11-19 — loudness momentary maks (EBU R128) tiap file diukur sekali
      (cache `tmp/sfx_loudness.json`); renderer menambah koreksi ke target −16.5 LUFS (true peak ≤ −1 dBTP), bunyi
      transien lewat limiter. File asli tidak diubah. Temuan: rentang awal 56 dB — `sfx_whoosh`/`sfx_swoosh`/`sfx_tap`
      (−45…−56 LUFS) nyaris tak terdengar (whoosh 8A-2 dulu senyap), `sfx_glitch` clipping (+12 dBTP). Sesudah:
      23 SFX, simpangan rata-rata 0.5 dB (maks 4.7 dB pada klik 70 ms). Prompt Gemini diberi skala volume.
      Mix akhir uji 258: −14.3 LUFS, true peak −1.4 dBTP.

*8C. B-roll (Pexels)* — ⏸ DITUNDA 2026-11-19 (API B-roll belum bisa dipakai; lanjut setelah key Pexels/Pixabay tersedia)
- [ ] **8C-1 Key & pencarian.** Key Pexels di Settings; modul cari video vertikal, unduh ke cache, catat
      pembuat + URL. Uji manual dengan 3 kata kunci. Belum masuk Auto Edit.
- [ ] **8C-2 Renderer cutaway.** Op plan `broll {from_word, to_word, query}`: tampil 1.5–3 dtk penuh layar
      (crop vertikal), suara pembicara & subtitle tetap jalan, masuk/keluar dengan cut atau zoom halus.
      Uji dengan plan tulisan tangan.
- [ ] **8C-3 Gemini memilih momen B-roll.** Hanya saat kalimat menyebut hal yang bisa divisualkan; maks 2–3
      per klip; dilarang di punchline/tawa/reaksi; LARANGAN KERAS visual tokoh agama/sosok sakral (konten
      keagamaan → visual netral). Validator menegakkan.
- [ ] **8C-4 Tinjau B-roll.** Thumbnail B-roll terpakai tampil di ringkasan; tombol "render ulang tanpa B-roll
      ini". (Gemini tidak bisa melihat B-roll — pemilihan dari metadata.)

*8D. Lapisan visual*
- [x] **8D-1 Transisi visual di cut kamera** ✅ 2026-11-19 — di cut ganti layout (kecuali beat tenang): zoom "settle"
      1.06→1.0 dalam 0.35 dtk (default) atau flash putih 2–3 frame (`eq` per frame); persona `transition_visual`
      zoom|flash|mix|none. Uji 258: 8 transisi.
- [x] **8D-2 Emoji stiker** ✅ 2026-11-19 — Noto Color Emoji dari font sistem (tanpa unduh, OFL) → PNG; Gemini memilih
      emoji (22 pilihan) + kata; pop-in 0.24 dtk + fade-out, di sisi kiri/kanan bergantian, menghindari wajah &
      subtitle; maks 3/klip, jarak ≥4 dtk, emoji tawa dilarang di momen haru.
- [x] **8D-3 Label nama** ✅ 2026-11-19 — pembicara bernama (keyakinan ≥0.5, bukan label peran generik) saat pertama
      tampil ≥1.5 dtk: kotak warna aksen persona + peran (Host/Tamu), lower third di sel orang itu (naik bila kena
      wajah), 2.5 dtk; subtitle menghindari label. Perbaikan saat uji: semula menutupi mic/mulut & terlalu kecil.
- [x] **8D-4 Progress bar** ✅ 2026-11-19 — garis 8 px di atas, warna highlight persona, maju sesuai waktu;
      persona `progress_bar`.

*8E. Musik*
- [x] **8E-1 Importer musik** ✅ 2026-11-19 — tercakup di 8B-1 (Impor Audio → jenis Musik: judul, mood, energi,
      lisensi + teks atribusi; BPM/durasi diukur otomatis).
- [x] **8E-2 Musik mengikuti momen** ✅ 2026-11-19 — `music.music_automation`: kurva volume (di atas ducking) —
      naik s.d. +4 dB sepanjang beat build, berhenti (−30 dB) 0.45 dtk sebelum build-up lalu masuk +3 dB di hit
      dan kembali dalam 1.5 dtk, −4 dB di puncak emosi, +3 dB di end card. Persona `music_follow_beats`.
      Diukur pada rantai musik terisolasi: build +3.9 dB, drop −30.0 dB, setelah hit +2.6 dB, end card +3.0 dB.
      Diuji dengan trek sintetis — library musik masih kosong.

Urutan kerja: 8A ✅ → 8B ✅ → 8D-1 → 8D-2 → 8D-3 → 8D-4 → 8E → 8C (ditunda) … (satu per sesi, tunggu penilaian user).

**Perbaikan 2026-11-19: Auto Edit macet di langkah Gemini** — `core/gemini_client.py` tidak punya batas waktu;
permintaan yang menggantung (server sibuk) membuat job menunggu selamanya (kejadian: klip 266, 26 menit tanpa
kemajuan). Sekarang: batas 300 dtk per panggilan (`GEMINI_TIMEOUT_S`), timeout → langsung model berikutnya, pesan
error membedakan server sibuk / tidak ada internet / kuota. Diuji dengan server lokal yang tidak pernah menjawab.

**Fase 9: Upload & jadwal YouTube** — langkah kecil, satu per sesi
- [x] **9-1 Upload private + jadwal tayang** ✅ 2026-11-19 — `core/youtube_upload.py` (upload resumable via `requests`,
      potongan 8 MB, retry 5xx), scope `youtube.upload` ditambahkan (akun lama: tombol "Hubungkan ulang (izin upload)"
      di halaman YouTube; callback OAuth kini menyimpan PKCE code_verifier). Selalu PRIVATE; jadwal = `status.publishAt`
      (min. 15 menit ke depan, zona channel). Isian awal dari kandidat (judul/deskripsi/hashtag) + kredit musik otomatis.
      Tabel `yt_uploads`, panel "Upload ke YouTube" di Editor setelah render, daftar "Upload & jadwal" di halaman
      YouTube; video otomatis terhubung ke Learning. Diuji dengan tiruan API (7 potongan untuk 28.6 MB, jadwal
      19:00 WIB → 12:00Z, error izin jelas). Upload sungguhan BELUM — user menguji saat simulasi end-to-end.
      ⚠ Proyek Google Cloud yang belum lolos audit YouTube API: video API terkunci private, jadwal publik tidak jalan.
- [x] **9-2 Saran jam tayang** ✅ 2026-11-19 — `upload_schedule.suggest_slots` (tanpa Gemini): median views per blok
      3 jam dari upload yang TIDAK berdempet; skor dalam 15% dari terbaik dianggap setara → pilih paling cepat; jarak
      ≥120 menit dari video tayang/terjadwal, maks 3/hari, minimal 60 menit dari sekarang. Endpoint
      `/api/youtube/schedule/suggest`; chip saran di panel upload (slot pertama jadi default). Catatan jujur ikut
      tampil: data = waktu upload sendiri (bukan audiens online); beda antar jam di channel ini kecil (1.032–1.385).
- [x] **9-3 Antrean beberapa klip** ✅ 2026-11-19 — kartu "Jadwalkan beberapa klip" di halaman YouTube: render Auto Edit
      yang belum diupload → pilih maks 5 (batas kuota) → slot berjarak aman dibagikan otomatis (judul & waktu bisa
      diubah) → upload berurutan satu per satu; satu gagal tidak menghentikan sisanya. Semua item divalidasi dulu
      (jadwal antar item ≥60 menit) sebelum dicatat. Diuji dengan tiruan API: 3 render → 18:00/20:00/22:00 WIB.
      Bug ditemukan saat uji & diperbaiki: antrean yang ditolak sempat tercatat setengah.

## 11. Keputusan

| # | Topik | Keputusan |
|---|---|---|
| 1 | Sumber musik | **Wajib bebas hak cipta.** Simpan bukti lisensi per lagu (sumber, URL, jenis lisensi, wajib atribusi atau tidak) di metadata library. Lagu tanpa catatan lisensi tidak boleh dipilih renderer. |
| 2 | Persona | **Satu dulu** (channel Stefra). Format tetap mendukung banyak persona nanti. |
| 3 | Gemini menonton video | **Tidak.** API yang dipakai versi gratis, jadi Gemini hanya menerima teks + sinyal. Konsekuensi: kualitas analisis bergantung penuh pada context pack (transkrip per kata, emosi audio, ekspresi, pembicara per shot). Panggilan Gemini per klip juga dihemat karena kuota gratis terbatas. |
| 4 | Persetujuan sebelum render | **Tidak ada.** Plan langsung dirender. Konsekuensi: validator (bagian 8) wajib ketat, dan plan + log koreksi validator disimpan agar bisa diperiksa setelahnya. |
