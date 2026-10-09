"""Minta Gemini membaca metrik channel sendiri dan memberi saran.

Bedanya dengan `gemini_analyzer.py`: modul itu menilai CALON clip dari transcript,
modul ini menilai HASIL clip yang sudah tayang. Yang satu menebak ke depan, yang
satu belajar ke belakang.

Bahaya utama fitur seperti ini bukan model yang salah hitung, tapi model yang
terdengar yakin di atas data yang terlalu sedikit — 3 video bukan bukti, dan
retensi dari 2 penonton bukan angka. Karena itu penyaringan sampel dilakukan di
sini, di Python, sebelum apa pun dikirim: video di bawah ambang view tetap
dikirim tapi ditandai TIDAK LAYAK SIMPUL, dan prompt melarang menarik kesimpulan
darinya.
"""

import logging

from google.genai import types

from core import gemini_client
from utils.constants import (
    CHANNEL_MIN_VIEWS,
    MAX_CLIP_SECONDS,
    MIN_CLIP_SECONDS,
)
from utils.helpers import seconds_to_timestamp

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """
Kamu analis konten short-form yang membaca data performa sebuah channel dan
memberi saran yang bisa langsung dikerjakan.

ATURAN PALING KERAS — KEJUJURAN STATISTIK:
- Tiap klaim WAJIB menunjuk angka yang benar-benar ada di data. Dilarang
  menyebut pola yang tidak bisa kamu tunjuk baris datanya.
- Video bertanda TIDAK LAYAK SIMPUL punya penonton terlalu sedikit. Boleh kamu
  sebut sebagai catatan, TAPI DILARANG menariknya jadi dasar kesimpulan. Retensi
  dari segelintir penonton adalah derau, bukan sinyal.
- Kalau jumlah video yang layak kurang dari 8, kamu WAJIB menyatakan di awal
  bahwa datanya belum cukup untuk menyimpulkan apa pun, lalu ubah nada seluruh
  jawaban dari "temuan" jadi "hipotesis yang perlu diuji".
- DILARANG mengarang sebab-akibat. "Video 58 detik retensinya paling tinggi"
  adalah fakta; "durasi 58 detik menyebabkan retensi tinggi" adalah karangan
  kalau sampelnya cuma segelintir.
- Kalau dua penjelasan sama masuk akal, sebut dua-duanya. Jangan pilih satu
  demi terdengar tegas.

FORMAT JAWABAN — markdown, dalam Bahasa Indonesia:

## Kondisi data
Satu paragraf jujur: berapa video yang layak disimpulkan, dan sejauh mana
kesimpulan boleh ditarik. Kalau lemah, katakan lemah.

## Yang terbaca dari angka
2-4 poin. Tiap poin menyebut angkanya. Bedakan tegas mana FAKTA dan mana DUGAAN.

## Hipotesis yang layak diuji
2-3 hipotesis konkret, masing-masing dengan cara mengujinya lewat video
berikutnya. Bentuknya: "kalau X benar, video berikutnya yang begini akan begitu."

## Yang bisa dikerjakan sekarang
Langkah konkret, urut dari dampak terbesar. Kalau salah satunya adalah mengubah
rentang durasi clip, sebut ANGKA rentangnya. Kalau datanya belum cukup untuk
menyarankan perubahan, katakan begitu — jangan mengarang pekerjaan.

## Yang perlu dikumpulkan dulu
Apa yang harus ada supaya pertanyaan yang belum terjawab bisa dijawab.

Jangan memuji, jangan basa-basi, jangan menutup dengan semangat-semangatan.
"""


def _format_video(video: dict, status: str) -> str:
    """status: 'ok' | 'sedikit_penonton' | 'belum_ada_retensi'.

    Dua sebab terakhir sengaja dibedakan. Video 1195 views yang belum punya
    angka retensi bukan "penontonnya sedikit" — datanya memang belum keluar
    karena YouTube Analytics tertinggal beberapa hari dari view count. Menyatukan
    keduanya membuat model menyimpulkan hal yang salah tentang video yang justru
    berkinerja bagus.
    """
    duration = video.get("duration_seconds") or 0
    retention = video.get("avg_view_percentage")
    watched = video.get("avg_view_seconds")

    mark = {
        "ok": "",
        "sedikit_penonton": "  [TIDAK LAYAK SIMPUL: penonton terlalu sedikit]",
        "belum_ada_retensi": (
            "  [BELUM ADA DATA RETENSI: kemungkinan terlalu baru, "
            "bukan berarti performanya buruk]"
        ),
    }[status]
    line = (
        f"- \"{video.get('title', '')}\"{mark}\n"
        f"  durasi {duration:.0f}s · {video.get('views', 0)} views"
    )
    if retention is not None:
        line += f" · retensi {retention:.1f}%"
    if watched is not None:
        line += f" · rata-rata ditonton {watched:.0f}s"
    if video.get("published_at"):
        line += f" · tayang {video['published_at'][:10]}"
    if video.get("likes") is not None:
        # Retensi dan keterlibatan bisa berlawanan arah pada video yang sama —
        # ditonton habis tapi tidak ada yang menekan like berarti sesuatu yang
        # berbeda dari ditinggalkan di tengah.
        line += (
            f"\n  {video.get('likes', 0)} like · "
            f"{video.get('comments', 0)} komentar · "
            f"{video.get('shares', 0)} share · "
            f"{video.get('subs_gained', 0)} subscriber baru"
        )

    curve = video.get("retention_curve")
    if curve and duration:
        points = [
            f"{point['ratio'] * duration:.0f}s={point['watch_ratio'] * 100:.0f}%"
            for point in curve[::max(1, len(curve) // 8)]
        ]
        line += "\n  kurva retensi: " + ", ".join(points)

    return line


def build_prompt(videos: list[dict], channel: dict | None = None) -> str:
    """Rakit data channel jadi teks. Penyaringan sampel terjadi di sini."""
    scored = [v for v in videos if v.get("avg_view_percentage") is not None]
    eligible = [v for v in scored if (v.get("views") or 0) >= CHANNEL_MIN_VIEWS]

    header = []
    if channel:
        header.append(f"Channel: {channel.get('channel_title', '')}")
        if channel.get("subscribers") is not None:
            header.append(f"Subscriber: {channel['subscribers']}")
    header.append(f"Video terdata: {len(videos)}")
    header.append(f"Punya metrik retensi: {len(scored)}")
    header.append(
        f"Layak disimpulkan (>= {CHANNEL_MIN_VIEWS} views): {len(eligible)}"
    )
    header.append(
        f"Rentang durasi clip yang dipakai tool saat ini: "
        f"{MIN_CLIP_SECONDS}-{MAX_CLIP_SECONDS} detik"
    )

    if eligible:
        retentions = [v["avg_view_percentage"] for v in eligible]
        header.append(
            f"Retensi rata-rata pada video layak: "
            f"{sum(retentions) / len(retentions):.1f}%"
        )
        durations = [v.get("duration_seconds") or 0 for v in eligible]
        header.append(
            f"Durasi video layak: {min(durations):.0f}-{max(durations):.0f} detik"
        )

    pending = [v for v in videos if v.get("avg_view_percentage") is None]
    if pending:
        header.append(
            f"Belum ada data retensi untuk {len(pending)} video "
            f"(YouTube Analytics tertinggal beberapa hari dari view count)"
        )

    eligible_ids = {v["video_id"] for v in eligible}

    def _status(video: dict) -> str:
        if video["video_id"] in eligible_ids:
            return "ok"
        if video.get("avg_view_percentage") is None:
            return "belum_ada_retensi"
        return "sedikit_penonton"

    body = [
        _format_video(v, _status(v))
        for v in sorted(videos, key=lambda v: -(v.get("views") or 0))
    ]

    return (
        "\n".join(header)
        + "\n\nDaftar video, urut dari views terbanyak:\n"
        + "\n".join(body)
        + "\n\nBerikan analisismu sesuai format yang diminta."
    )


def build_performance_hint(
    videos: list[dict], min_views: int = CHANNEL_MIN_VIEWS
) -> str | None:
    """Ringkas performa upload sendiri, untuk dititipkan ke pemilihan clip.

    Ini yang menutup lingkarannya: hasil upload sebelumnya ikut menentukan clip
    berikutnya. Bahayanya jelas — dengan sampel kecil, model akan meniru tiga
    video terakhir seolah itu hukum alam. Karena itu ringkasan ini SELALU
    menyebut berapa video yang jadi dasarnya dan, saat sampelnya sedikit,
    memerintahkan secara eksplisit agar diperlakukan sebagai kecenderungan
    lemah. Return None kalau tidak ada apa pun yang layak dijadikan dasar.
    """
    eligible = [
        v for v in videos
        if v.get("avg_view_percentage") is not None
        and (v.get("views") or 0) >= min_views
    ]
    if not eligible:
        return None

    # Diurutkan berdasarkan REAKSI (share + komentar per view), bukan retensi.
    # Meranking dengan retensi terlihat masuk akal tapi menyesatkan: retensi
    # channel ini sudah sehat (60-88%) sementara share-nya 0,015%, jadi daftar
    # berurut-retensi mengajari model mengejar satu-satunya hal yang sudah beres.
    def _reaction_rate(video: dict) -> float:
        views = video.get("views") or 0
        if not views:
            return 0.0
        return ((video.get("shares") or 0) + (video.get("comments") or 0)) / views

    ranked = sorted(eligible, key=lambda v: -_reaction_rate(v))
    lines = [
        "PERFORMA CLIP YANG SUDAH KAMU UPLOAD "
        f"({len(eligible)} video punya penonton cukup untuk dibaca). "
        "Diurutkan dari yang paling memancing reaksi:"
    ]
    for video in ranked:
        duration = video.get("duration_seconds") or 0
        lines.append(
            f"- {duration:.0f} detik → {video.get('views', 0)} view, "
            f"retensi {video['avg_view_percentage']:.1f}%, "
            f"{video.get('likes') or 0} like, "
            f"{video.get('comments') or 0} komentar, "
            f"{video.get('shares') or 0} share "
            f"— \"{video.get('title', '')[:70]}\""
        )

    total_views = sum(v.get("views") or 0 for v in eligible)
    total_shares = sum(v.get("shares") or 0 for v in eligible)
    total_comments = sum(v.get("comments") or 0 for v in eligible)
    retentions = [v["avg_view_percentage"] for v in eligible]
    avg_retention = sum(retentions) / len(retentions)

    lines.append(
        f"\nTOTAL: {total_views} view → {total_comments} komentar, "
        f"{total_shares} share. Rata-rata retensi {avg_retention:.1f}%."
    )

    # Ambang kasar: share sehat ~0,5% dan komentar sehat ~0,1% dari view. Di
    # bawah itu, masalahnya bukan clip-nya kurang menahan penonton.
    if total_views and (total_shares / total_views) < 0.005:
        lines.append(
            "BACA INI BAIK-BAIK: retensinya sudah sehat, tapi share dan "
            "komentarnya nyaris nol. Artinya orang menonton sampai habis lalu "
            "tidak merasakan apa-apa. Menambah clip yang 'enak ditonton' TIDAK "
            "akan memperbaiki ini. Yang dibutuhkan clip yang memancing orang "
            "bergerak: pendapat yang membelah, momen yang bikin orang teringat "
            "temannya, atau klaim yang bikin orang gatal membantah. "
            "Prioritaskan `shareability` dan `discussion_trigger` di atas "
            "kerapian potongan."
        )

    if len(eligible) < 8:
        lines.append(
            "PERINGATAN: sampel ini KECIL. Perlakukan sebagai kecenderungan "
            "lemah, bukan aturan. DILARANG menolak kandidat yang kuat hanya "
            "karena durasinya berbeda dari daftar di atas. Pakai ini untuk "
            "memutuskan saat dua kandidat sama kuatnya, bukan sebagai penyaring."
        )
    else:
        lines.append(
            "Condongkan pilihanmu ke pola yang terbukti di atas, tapi jangan "
            "membuang kandidat yang jelas lebih kuat hanya demi kecocokan pola."
        )

    return "\n".join(lines)


SETUP_SYSTEM_PROMPT = """
Kamu konsultan channel YouTube yang memeriksa SETUP sebuah channel — identitas,
metadata, dan konsistensi — lalu memberi perbaikan konkret.

ATURAN PALING KERAS — SARAN HARUS COCOK DENGAN SUMBER TRAFIK:
Data berisi rincian dari mana view benar-benar datang. Itu menentukan saran mana
yang berguna dan mana yang buang waktu.
- Kalau mayoritas view datang dari feed Shorts atau feed rekomendasi lain,
  distribusinya ALGORITMIK. Tag, keyword, dan optimasi pencarian nyaris tidak
  berpengaruh di sana. DILARANG menyuruh user merapikan tag demi SEO kalau
  trafik pencariannya kecil — katakan terus terang bahwa dampaknya kecil, dan
  arahkan tenaganya ke hal yang benar-benar menggerakkan feed.
- Kalau trafik pencarian besar, barulah metadata pencarian layak diprioritaskan.
- Selalu sebut angka trafiknya saat kamu memakai alasan ini.

ATURAN KEJUJURAN:
- Tunjuk angka atau contoh nyata untuk tiap klaim. Dilarang memberi nasihat
  umum yang bisa ditempel ke channel mana pun.
- Bedakan mana yang jelas salah (mis. field kosong, kategori tidak konsisten)
  dari mana yang cuma dugaan.
- Channel kecil dengan sedikit video: jangan berlagak tahu penyebab. Sebut
  keterbatasannya.
- Jangan menyuruh mengerjakan sesuatu yang dampaknya kecil hanya supaya
  daftarnya terlihat panjang. Kalau sebuah bagian sudah cukup baik, katakan
  sudah cukup baik.
- DILARANG menyebut angka pembanding industri ("rasio like yang sehat adalah
  3-5%", "rata-rata channel sejenis begini") — kamu tidak punya sumbernya dan
  angka itu akan dipercaya user sebagai fakta. Bandingkan video user dengan
  video user yang lain saja, karena hanya itu yang benar-benar ada di data.
- Video dengan penonton sangat sedikit (di bawah ~30) tidak boleh dipakai untuk
  menyimpulkan bahwa sebuah TOPIK gagal. Video yang tidak didistribusikan dan
  video yang ditolak penonton menghasilkan angka yang mirip, dan data ini tidak
  bisa membedakan keduanya. Sebut sebagai dugaan, bukan fakta.

FORMAT JAWABAN — markdown, Bahasa Indonesia:

## Dari mana penontonmu datang
Baca angka sumber trafiknya, lalu nyatakan konsekuensinya: usaha apa yang
berdampak dan apa yang sia-sia untuk channel ini.

## Yang jelas perlu dibereskan
Hal yang konkret salah atau kosong. Tiap poin sebut buktinya. Urut dari dampak
terbesar. Untuk tiap poin beri contoh perbaikan yang bisa langsung disalin.

## Pola di judul dan metadata
Bandingkan video yang performanya bagus dengan yang tidak — apa bedanya di
judul, panjang deskripsi, atau penggunaan hashtag. Bedakan fakta dari dugaan.

## Identitas channel
Nilai judul channel, deskripsi, keyword, dan handle. Kalau perlu diperbaiki,
TULISKAN versi penggantinya, jangan cuma bilang "perbaiki".

## Keterlibatan penonton
Baca rasio like, komentar, share, dan konversi subscriber terhadap view. Sebut
mana yang wajar dan mana yang timpang, lalu apa yang bisa mengubahnya.

## Urutan kerja
3-5 langkah, urut dari yang paling berdampak. Sebutkan yang TIDAK perlu
dikerjakan sekarang, dan kenapa.

Jangan memuji, jangan basa-basi.
"""


def build_setup_prompt(snapshot: dict) -> str:
    """Rakit snapshot setup channel jadi teks."""
    branding = snapshot.get("branding", {})
    videos = snapshot.get("videos", [])
    traffic = snapshot.get("traffic", [])
    engagement = snapshot.get("engagement", {})

    lines = ["=== IDENTITAS CHANNEL ==="]
    lines.append(f"Judul: {branding.get('title', '')}")
    lines.append(f"Handle: {branding.get('custom_url') or '(belum ada)'}")
    lines.append(f"Negara: {branding.get('country') or '(belum diisi)'}")
    lines.append(
        f"Subscriber: {branding.get('subscribers', 0)} · "
        f"Total view: {branding.get('total_views', 0)} · "
        f"Jumlah video: {branding.get('video_count', 0)}"
    )
    description = branding.get("description") or ""
    lines.append(
        f"Deskripsi channel ({len(description)} karakter): "
        f"{description!r}" if description else "Deskripsi channel: (KOSONG)"
    )
    keywords = branding.get("keywords") or ""
    lines.append(
        f"Keyword channel: {keywords!r}" if keywords else "Keyword channel: (KOSONG)"
    )

    total_traffic = sum(item["views"] for item in traffic) or 1
    lines.append("\n=== SUMBER TRAFIK ===")
    for item in traffic:
        share = item["views"] / total_traffic * 100
        lines.append(f"{item['source']}: {item['views']} view ({share:.1f}%)")

    if engagement:
        views = engagement.get("views") or 1
        lines.append("\n=== KETERLIBATAN (seluruh channel) ===")
        lines.append(
            f"{engagement.get('views', 0)} view · "
            f"{engagement.get('likes', 0)} like ({engagement.get('likes', 0) / views * 100:.2f}%) · "
            f"{engagement.get('comments', 0)} komentar · "
            f"{engagement.get('shares', 0)} share"
        )
        lines.append(
            f"Subscriber bertambah {engagement.get('subs_gained', 0)}, "
            f"berkurang {engagement.get('subs_lost', 0)}"
        )

    audience = snapshot.get("audience") or {}
    if audience.get("demographics"):
        lines.append("\n=== DEMOGRAFI PENONTON (% dari total) ===")
        for row in sorted(
            audience["demographics"], key=lambda r: -r["percentage"]
        )[:8]:
            lines.append(
                f"{row['age']} {row['gender']}: {row['percentage']:.1f}%"
            )
    if audience.get("countries"):
        lines.append("\n=== NEGARA ===")
        for row in audience["countries"]:
            lines.append(
                f"{row['country']}: {row['views']} view · "
                f"retensi {row['retention']:.1f}%"
            )
    if audience.get("devices"):
        total_device = sum(r["views"] for r in audience["devices"]) or 1
        lines.append("\n=== PERANGKAT ===")
        for row in audience["devices"]:
            lines.append(
                f"{row['device']}: {row['views']} view "
                f"({row['views'] / total_device * 100:.1f}%)"
            )
    if audience.get("locations"):
        lines.append("\n=== LOKASI PEMUTARAN ===")
        for row in audience["locations"]:
            lines.append(f"{row['location']}: {row['views']} view")

    lines.append("\n=== VIDEO ===")
    for video in sorted(videos, key=lambda v: -(v.get("views") or 0)):
        description = video.get("description") or ""
        tags = video.get("tags") or []
        lines.append(
            f"- \"{video.get('title', '')}\"\n"
            f"  {video.get('views', 0)} view · durasi "
            f"{video.get('duration_seconds') or 0:.0f}s · "
            f"tayang {(video.get('published_at') or '')[:10]} · "
            f"kategori {video.get('category_id')}\n"
            f"  deskripsi {len(description)} karakter"
            f"{' (KOSONG)' if not description else ''} · "
            f"{len(tags)} tag{' (KOSONG)' if not tags else ': ' + ', '.join(tags[:8])}"
        )
        if video.get("likes") is not None:
            lines.append(
                f"  {video.get('likes', 0)} like · "
                f"{video.get('comments', 0)} komentar · "
                f"{video.get('shares', 0)} share · "
                f"{video.get('subs_gained', 0)} subscriber baru"
            )

    lines.append(
        "\nCatatan: CTR thumbnail dan impresi TIDAK tersedia lewat API YouTube, "
        "jadi jangan menyimpulkan apa pun tentang performa thumbnail."
    )
    lines.append("\nBerikan analisismu sesuai format yang diminta.")
    return "\n".join(lines)


def advise_setup(snapshot: dict, api_key: str | None = None) -> str:
    """Saran perbaikan setup channel berdasarkan metadata video yang sudah tayang."""
    if not snapshot.get("videos"):
        raise ValueError("Belum ada video untuk diperiksa.")

    response, _model = gemini_client.generate(
        build_setup_prompt(snapshot),
        types.GenerateContentConfig(
            system_instruction=SETUP_SYSTEM_PROMPT, max_output_tokens=8192
        ),
        api_key,
    )

    text = (response.text or "").strip()
    if not text:
        raise Exception(
            "Gemini tidak mengembalikan teks. Coba lagi, atau cek kuota API key."
        )
    return text


def advise(
    videos: list[dict],
    channel: dict | None = None,
    api_key: str | None = None,
) -> str:
    """Kirim ringkasan channel ke Gemini, kembalikan sarannya sebagai markdown."""
    if not videos:
        raise ValueError("Belum ada data video untuk dianalisis.")

    response, _model = gemini_client.generate(
        build_prompt(videos, channel),
        types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT, max_output_tokens=8192
        ),
        api_key,
    )

    text = (response.text or "").strip()
    if not text:
        raise Exception(
            "Gemini tidak mengembalikan teks. Coba lagi, atau cek kuota API key."
        )
    return text
