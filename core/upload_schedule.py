"""Analisis jadwal upload: jam, jarak antar upload, dan jumlah per hari.

Yang bisa dan tidak bisa dijawab di sini ditentukan oleh API, bukan oleh selera:
YouTube Analytics TIDAK menyediakan dimensi jam maupun hari-dalam-minggu — sudah
diuji, keduanya ditolak dengan "Unknown identifier". Yang terhalus cuma `day`.
Laporan "kapan penontonmu ada di YouTube" hanya hidup di YouTube Studio.

Jadi semua di sini dihitung dari `publishedAt` video sendiri, bukan dari perilaku
penonton. Bedanya penting: ini menjawab "kapan kamu upload dan apa hasilnya",
bukan "kapan penontonmu online".
"""

import logging
from datetime import datetime, timedelta, timezone

from google.genai import types

from core import gemini_client
from utils.constants import CHANNEL_MIN_VIEWS, CHANNEL_TZ_OFFSET

logger = logging.getLogger(__name__)

# Jarak di bawah ini dianggap "berdempet". Pada data uji, dua video yang tayang
# 9 dan 10 menit setelah video sebelumnya hanya dapat 8 dan 2 view sementara
# yang berjarak lebih jauh rata-rata ratusan.
CLOSE_UPLOAD_MINUTES = 60


def _local(published_at: str, offset_hours: int = CHANNEL_TZ_OFFSET) -> datetime:
    """publishedAt (UTC) → waktu lokal audiens.

    Dipakai zona audiens, bukan zona server: jam upload cuma berarti kalau
    dibandingkan dengan jam penontonnya.
    """
    moment = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
    return moment.astimezone(timezone(timedelta(hours=offset_hours)))


WEEKDAYS = ["Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu"]


def analyze(videos: list[dict], offset_hours: int = CHANNEL_TZ_OFFSET) -> dict:
    """Susun riwayat upload jadi bentuk yang bisa dinilai."""
    dated = [v for v in videos if v.get("published_at")]
    if not dated:
        return {"uploads": [], "per_day": [], "offset_hours": offset_hours}

    ordered = sorted(dated, key=lambda v: v["published_at"])

    uploads = []
    previous: datetime | None = None
    for video in ordered:
        moment = _local(video["published_at"], offset_hours)
        gap_minutes = (
            (moment - previous).total_seconds() / 60 if previous else None
        )
        uploads.append({
            "title": video.get("title", ""),
            "local_time": moment,
            "hour": moment.hour,
            "weekday": WEEKDAYS[moment.weekday()],
            "date": moment.date().isoformat(),
            "views": video.get("views") or 0,
            "retention": video.get("avg_view_percentage"),
            "gap_minutes": gap_minutes,
        })
        previous = moment

    per_day: dict[str, list[dict]] = {}
    for upload in uploads:
        per_day.setdefault(upload["date"], []).append(upload)

    daily = [
        {
            "date": date,
            "count": len(group),
            "total_views": sum(u["views"] for u in group),
            "avg_views": sum(u["views"] for u in group) // len(group),
        }
        for date, group in sorted(per_day.items())
    ]

    close = [u for u in uploads if u["gap_minutes"] is not None
             and u["gap_minutes"] < CLOSE_UPLOAD_MINUTES]
    spaced = [u for u in uploads if u["gap_minutes"] is not None
              and u["gap_minutes"] >= CLOSE_UPLOAD_MINUTES]

    return {
        "uploads": uploads,
        "per_day": daily,
        "offset_hours": offset_hours,
        "close": close,
        "spaced": spaced,
        "close_avg_views": (
            sum(u["views"] for u in close) // len(close) if close else None
        ),
        "spaced_avg_views": (
            sum(u["views"] for u in spaced) // len(spaced) if spaced else None
        ),
    }


SYSTEM_PROMPT = """
Kamu penasihat jadwal upload untuk channel short-form. Kamu menilai KAPAN video
diunggah dan BERAPA BANYAK per hari, lalu memberi jadwal yang bisa dijalankan.

BATAS DATA — WAJIB kamu patuhi dan sampaikan:
- Data ini adalah waktu upload milik user sendiri beserta hasilnya. Ini BUKAN
  data kapan penonton sedang online. YouTube tidak menyediakan itu lewat API;
  angka itu hanya ada di YouTube Studio. Kalau jam upload jadi pertanyaan
  penting, SURUH user membukanya sendiri di Studio — jangan mengarang.
- Kamu TIDAK bisa memisahkan pengaruh jadwal dari pengaruh isi video. Video yang
  jeblok mungkin karena waktunya, mungkin karena kontennya. Sebut kemungkinan
  itu tiap kali kamu menyalahkan waktu.
- Jumlah video biasanya sedikit. Kalau kurang dari 15 upload, nyatakan di awal
  bahwa ini pola awal yang perlu diuji, bukan kesimpulan.
- DILARANG menyebut "jam terbaik untuk upload" versi umum yang beredar di
  internet. Kamu hanya boleh bicara berdasarkan angka yang ada di data ini.

Yang perlu kamu perhatikan khusus:
- JARAK ANTAR UPLOAD. Video yang tayang berdempet sering saling memakan
  distribusi — yang pertama dapat, sisanya mati. Periksa apakah pola itu ada di
  data, dan sebut angkanya.
- JUMLAH PER HARI. Bandingkan hari dengan banyak upload melawan hari dengan
  sedikit upload, pakai rata-rata view PER VIDEO, bukan totalnya.

FORMAT — markdown, Bahasa Indonesia:

## Yang terbaca dari jadwalmu
Poin-poin dengan angka. Bedakan FAKTA dari DUGAAN.

## Jarak antar upload
Apakah ada bukti upload berdempet merugikan? Sebut angkanya. Kalau tidak ada
buktinya, katakan tidak ada.

## Berapa video per hari
Angka konkret, dengan alasannya dari data.

## Jadwal yang kusarankan
Jadwal yang bisa langsung dijalankan minggu ini: berapa video, jam berapa, jarak
berapa. Kalau data belum cukup untuk memilih jam, katakan begitu dan sarankan
cara mengujinya.

## Yang harus kamu cek sendiri di YouTube Studio
Sebutkan laporan yang tidak bisa kuakses tapi akan menjawab sisanya.

Jangan basa-basi.
"""


def build_prompt(analysis: dict) -> str:
    uploads = analysis.get("uploads") or []
    if not uploads:
        raise ValueError("Belum ada riwayat upload untuk dianalisis.")

    offset = analysis.get("offset_hours", CHANNEL_TZ_OFFSET)
    lines = [
        f"Semua waktu dalam UTC+{offset} (zona mayoritas penonton).",
        f"Total upload terdata: {len(uploads)}",
        f"Ambang view agar retensi layak dibaca: {CHANNEL_MIN_VIEWS}",
        "",
        "=== RIWAYAT UPLOAD (urut waktu) ===",
    ]
    for upload in uploads:
        gap = upload["gap_minutes"]
        gap_label = (
            "upload pertama" if gap is None
            else f"{gap:.0f} menit setelah upload sebelumnya" if gap < 120
            else f"{gap / 60:.1f} jam setelah upload sebelumnya"
        )
        retention = upload["retention"]
        lines.append(
            f"- {upload['local_time'].strftime('%Y-%m-%d %H:%M')} "
            f"({upload['weekday']}) · {upload['views']} view"
            + (f" · retensi {retention:.1f}%" if retention is not None else "")
            + f" · {gap_label}\n"
            f"  \"{upload['title'][:70]}\""
        )

    lines.append("\n=== JUMLAH UPLOAD PER HARI ===")
    for day in analysis.get("per_day", []):
        lines.append(
            f"{day['date']}: {day['count']} video · total {day['total_views']} view "
            f"· rata-rata {day['avg_views']} view per video"
        )

    close_avg = analysis.get("close_avg_views")
    spaced_avg = analysis.get("spaced_avg_views")
    if close_avg is not None or spaced_avg is not None:
        lines.append("\n=== JARAK ANTAR UPLOAD ===")
        lines.append(
            f"Berdempet (< {CLOSE_UPLOAD_MINUTES} menit): "
            f"{len(analysis.get('close') or [])} video, rata-rata {close_avg} view"
        )
        lines.append(
            f"Berjarak (>= {CLOSE_UPLOAD_MINUTES} menit): "
            f"{len(analysis.get('spaced') or [])} video, rata-rata {spaced_avg} view"
        )

    lines.append("\nBerikan analisismu sesuai format yang diminta.")
    return "\n".join(lines)


def advise(videos: list[dict], api_key: str | None = None) -> str:
    """Saran jadwal upload dari riwayat sendiri."""
    response, _model = gemini_client.generate(
        build_prompt(analyze(videos)),
        types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT, max_output_tokens=8192
        ),
        api_key,
    )
    text = (response.text or "").strip()
    if not text:
        raise Exception("Gemini tidak mengembalikan teks. Coba lagi.")
    return text
