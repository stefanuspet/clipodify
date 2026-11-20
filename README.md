# Clipodify

Editor klip podcast otomatis bertenaga AI. Dari video podcast panjang, Clipodify memotong momen terbaik, mengedit jadi portrait 1080×1920 untuk TikTok/Reels/Shorts — lengkap dengan subtitle, SFX, transisi, label nama, emoji stiker, dan jadwal upload ke YouTube — tanpa menyentuh timeline secara manual.

---

## Prasyarat

| Kebutuhan | Versi minimum | Catatan |
|---|---|---|
| Python | 3.10+ | 3.12 direkomendasikan |
| Node.js | 18+ | Untuk frontend |
| FFmpeg | 6.0+ | Lihat instalasi di bawah |
| CUDA GPU | RTX 3050 atau setara | Untuk Whisper; CPU bisa tapi lambat |
| Gemini API key | — | Gratis di [AI Studio](https://aistudio.google.com/apikey) |

### Instalasi FFmpeg

**Ubuntu/Debian**
```bash
sudo apt install ffmpeg
```

**Windows** — unduh dari [ffmpeg.org/download](https://ffmpeg.org/download.html), ekstrak, lalu tambahkan folder `bin/` ke PATH.

**macOS**
```bash
brew install ffmpeg
```

---

## Setup

### 1. Clone & buat virtual environment

**Linux/macOS**
```bash
git clone <repo-url> clipodify
cd clipodify
python3 -m venv venv
source venv/bin/activate
```

**Windows (PowerShell)**
```powershell
git clone <repo-url> clipodify
cd clipodify
python -m venv venv
venv\Scripts\Activate.ps1
```

### 2. Install dependensi Python

```bash
pip install -r requirements.txt
```

> Kalau tidak punya GPU NVIDIA, ganti baris `torch` di `requirements.txt` dengan versi CPU:
> ```
> torch>=2.1.0+cpu --extra-index-url https://download.pytorch.org/whl/cpu
> ```
> Transkripsi tetap jalan, tapi lebih lambat sekitar 5–10×.

### 3. Install dependensi frontend

```bash
cd frontend
npm install
cd ..
```

### 4. Konfigurasi

```bash
cp .env.example .env
```

Buka `.env`, isi minimal:

```env
GEMINI_API_KEY=AIza...        # wajib
OUTPUT_DIR=~/Downloads/clipodify
```

Untuk Windows, gunakan path dengan backslash atau forward slash:
```env
OUTPUT_DIR=C:/Users/NamaAnda/Downloads/clipodify
```

Semua variabel lain sudah ada default yang masuk akal.

### 5. (Opsional) Google OAuth untuk YouTube Analytics & Upload

Diperlukan hanya untuk halaman YouTube. Lihat bagian **Koneksi YouTube** di bawah.

---

## Menjalankan aplikasi

Dua proses perlu dijalankan bersamaan: backend Python dan frontend React.

**Terminal 1 — Backend**

Linux/macOS:
```bash
source venv/bin/activate
uvicorn backend.main:app --reload --port 8000
```

Windows (PowerShell):
```powershell
venv\Scripts\Activate.ps1
uvicorn backend.main:app --reload --port 8000
```

**Terminal 2 — Frontend**

```bash
cd frontend
npm run dev
```

Buka browser: **http://localhost:5173**

---

## Alur kerja

### 1. Analyze — temukan klip terbaik
Masukkan URL video YouTube atau upload file video lokal. Clipodify:
- Mentranskripsi dengan Whisper (GPU jika ada)
- Mendeteksi wajah, orang, dan ekspresi
- Memilih 10–15 kandidat momen dengan Gemini

### 2. Editor — edit satu klip
Pilih kandidat, lalu tekan **Auto Edit**. Gemini menyusun rencana:
- Framing & zoom ke orang yang berbicara
- Subtitle bergaya dengan highlight per kata
- SFX (whoosh di transisi, build-up sebelum momen penting)
- Grade warna, stiker emoji, label nama pembicara
- Progress bar tipis di atas

Render menghasilkan file MP4 portrait 1080×1920 siap upload.

### 3. YouTube — jadwalkan upload
Hubungkan akun YouTube di halaman YouTube, lalu upload satu klip atau jadwalkan beberapa klip sekaligus dengan jarak aman otomatis.

---

## Koneksi YouTube (opsional)

Untuk upload dan melihat analytics channel:

1. Buat proyek di [Google Cloud Console](https://console.cloud.google.com/)
2. Aktifkan **YouTube Data API v3** dan **YouTube Analytics API**
3. Buat OAuth 2.0 client (tipe: Desktop app)
4. Unduh file `client_secret_*.json`
5. Di aplikasi, buka halaman **YouTube** → paste isi file JSON → klik Connect
6. Setujui izin di browser

> **Penting:** Proyek Google Cloud yang belum lolos [audit YouTube API](https://developers.google.com/youtube/v3/guides/auth/client-side-web-apps) hanya bisa mengupload video sebagai private permanen — jadwal tayang publik tidak berjalan sampai audit disetujui. Ini gratis tapi prosesnya beberapa minggu.

---

## Model AI yang digunakan

| Model | Kapan dipakai |
|---|---|
| `gemini-3.6-flash` | Default untuk semua langkah (plan edit, analisis kandidat) |
| `gemini-3.5-flash` | Cadangan otomatis jika model utama sedang sibuk/error |
| `gemini-3.5-flash-lite` | Cadangan kedua; lebih hemat kuota |

Semua model bisa diganti di `.env` (`GEMINI_MODEL`, `GEMINI_FALLBACK_MODELS`).

Jika model tidak menjawab dalam 5 menit (server sibuk), sistem otomatis pindah ke model berikutnya. Batas waktu bisa diubah via `GEMINI_TIMEOUT_S` di `.env`.

---

## Catatan Windows

Beberapa hal yang berbeda di Windows:

- **Font emoji:** Noto Color Emoji otomatis di-cache dari font sistem (Linux) atau dari file yang di-bundel. Di Windows, emoji stiker akan menggunakan Segoe UI Emoji jika tersedia.
- **Path separator:** `.env` bisa pakai forward slash (`C:/Users/...`) atau backslash double (`C:\\Users\\...`).
- **Multiprocessing:** Backend menggunakan spawn-based multiprocessing, sudah kompatibel dengan Windows.
- **CUDA:** Pastikan driver NVIDIA dan CUDA toolkit versi yang kompatibel dengan PyTorch sudah terinstal. Cek [pytorch.org/get-started](https://pytorch.org/get-started/locally/) untuk perintah install yang tepat.

---

## Variabel `.env` penting

| Variabel | Default | Keterangan |
|---|---|---|
| `GEMINI_API_KEY` | — | **Wajib diisi** |
| `OUTPUT_DIR` | `~/Downloads/clipodify` | Folder output video |
| `WHISPER_MODEL` | `large-v2` | Ukuran model Whisper (`tiny`, `base`, `small`, `medium`, `large-v2`) |
| `WHISPER_DEVICE` | `cuda` | Ganti ke `cpu` jika tidak ada GPU |
| `WHISPER_COMPUTE_TYPE` | `float16` | Ganti ke `int8` atau `float32` untuk GPU dengan VRAM terbatas |
| `GEMINI_TIMEOUT_S` | `300` | Batas waktu panggilan Gemini (detik) |
| `CHANNEL_TZ_OFFSET` | `7` | Zona waktu channel untuk saran jadwal upload (WIB = 7) |

Semua opsi lengkap ada di `.env.example`.

---

## Troubleshooting

**"No module named X"**
Pastikan virtual environment aktif sebelum menjalankan backend.

**Whisper gagal / keluar memori**
Ganti `WHISPER_MODEL=medium` atau `small` di `.env`, dan `WHISPER_COMPUTE_TYPE=int8`.

**Gemini error "model not available"**
Model `gemini-2.5-flash` dan lebih lama sudah tidak tersedia untuk API key baru. Pakai `gemini-3.6-flash` (sudah jadi default).

**Auto Edit macet lama**
Normal jika Gemini sedang sibuk — sistem akan coba model cadangan secara otomatis. Jika lebih dari 20 menit, restart backend dan coba lagi.

**Upload YouTube gagal "insufficient permissions"**
Klik "Hubungkan ulang (izin upload)" di halaman YouTube. Koneksi awal hanya untuk analytics; upload butuh izin tambahan yang diminta sekali.

**Frontend tidak bisa terhubung ke backend**
Pastikan backend berjalan di port 8000 dan frontend di port 5173. Kalau port berbeda, atur `VITE_API_URL` di `frontend/.env.local`.

---

## Lisensi

Kode sumber: MIT.

Dependensi pihak ketiga memiliki lisensi masing-masing. Yang perlu diperhatikan:
- **FFmpeg**: LGPL/GPL tergantung konfigurasi build
- **Noto Color Emoji**: SIL Open Font License 1.1
- **InsightFace**: MIT
- **Ultralytics (YOLO)**: AGPL-3.0 untuk penggunaan open-source; lisensi komersial tersedia
