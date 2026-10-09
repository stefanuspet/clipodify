# Clipodify

AI-powered clip finder untuk podcast & interview. Mengidentifikasi momen
berpotensi viral lewat 4 sinyal prefilter + Gemini sebagai judge.

## Requirements

- Python 3.10+
- CUDA GPU (RTX 3050 atau lebih) untuk Whisper
- ffmpeg (`sudo apt install ffmpeg`)

## Setup

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# Edit .env, isi GEMINI_API_KEY
```

Ambil API key gratis di [Google AI Studio](https://aistudio.google.com/apikey).

## Run

```bash
streamlit run app.py
```

Buka browser: http://localhost:8501

## Cara kerja

Dua tahap. **Prefilter** (murah, lokal) menyaring video jadi ~30 lingkungan
kandidat — tugasnya bukan meranking dengan presisi, tapi memastikan momen bagus
tidak hilang sebelum Gemini melihatnya. **Gemini** menerima transcript utuh tiap
kandidat plus 60 detik konteks sebelum & sesudah, lalu menentukan sendiri titik
potong yang tepat.

| Sinyal | Bobot | Mengukur |
|---|---|---|
| Comment mention | 30% | Bukti nyata penonton (bukan proxy) |
| Audio energy + shape | 25% | Intensitas & letak puncaknya |
| Narrative (tanya→jawab) | 25% | Bentuk cerita, bukan tingkat |
| Text heuristic | 20% | Kepadatan, angka |
| Ad-read detection | drop | Iklan sponsor dibuang total |

Kalau video tidak punya cukup mention timestamp di komentar (default: minimal 5),
bobot comment dinormalisasi ulang ke tiga sinyal sisanya.

Hasil Gemini divalidasi berlapis: skala skor dinormalisasi, timestamp di-snap ke
transcript asli (kandidat yang meleset >30 detik dibuang sebagai halusinasi),
durasi difilter keras 30–90 detik, kandidat berdekatan di-dedupe, dan variasi
`clip_type` dijaga supaya daftar akhirnya bisa dipilih.

## Model AI

Default: `gemini-3.6-flash`.

> **Penting:** `gemini-2.5-flash` dan yang lebih lama **tidak tersedia untuk API
> key baru** (404 "no longer available to new users"). Model 1.5.x dan 2.0.x
> sudah shut down sepenuhnya.

Alternatif hemat kuota: `gemini-3.5-flash-lite` — sekitar 3× lebih hemat token,
tapi cenderung memilih potongan lebih pendek sehingga lebih banyak kandidat
gugur di filter durasi.

Cek model yang aktif untuk akunmu di
[AI Studio](https://aistudio.google.com/rate-limit). Project ini memakai SDK
`google-genai`, bukan `google-generativeai` yang sudah deprecated.

## Tuning

Semua bobot dan threshold ada di `.env`. Kalau hasilnya kurang bagus, yang paling
berpengaruh bukan bobot sinyal tapi `PAUSE_GAP_THRESHOLD` (default 1.2) — coba
0.8 atau 1.6 dulu sebelum menyentuh yang lain.

Statistik tiap tahap ditampilkan di UI saat processing. Kalau hasilnya sedikit,
angka itu memberitahu di mana hilangnya — "Gemini kirim 15, snap membuang 11"
adalah masalah yang sangat berbeda dari "Gemini cuma mengembalikan 4".

## Batasan

- Hanya untuk konten berbicara. Video musik/instrumental menghasilkan transcript
  kosong karena VAD membuang semuanya sebagai non-speech.
- Sinyal comment hanya berguna untuk video yang sudah punya penonton.

## Output

Clip tersimpan di `~/clipodify_output/{video_title}/`
Format: MP4, kualitas original, no re-encode.
