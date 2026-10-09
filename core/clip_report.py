"""Berkas analisis yang menyertai tiap clip yang diunduh.

Alasannya sederhana: file .mp4 yang mendarat di folder output tidak membawa apa
pun tentang kenapa ia dipilih. Seminggu kemudian, "clip_03_00-12-34.mp4" tidak
memberi tahu apa hook-nya, caption apa yang sudah disiapkan, atau bagian mana
yang harus dipindahkan ke depan saat mengedit.

Berkas ini ditulis berdampingan dengan videonya, nama sama, ekstensi .md.
"""

import os

from utils.constants import (
    CLIP_ATTRIBUTION,
    CLIP_CAMPAIGN_ATTRIBUTION,
    CLIP_CAMPAIGN_HASHTAGS,
    CLIP_FIXED_HASHTAGS,
)
from utils.helpers import seconds_to_timestamp


def _slug(value: str) -> str:
    """'Deddy Corbuzier' -> 'deddycorbuzier'. Untuk dijadikan hashtag."""
    return "".join(char for char in (value or "").lower() if char.isalnum())


def parse_hashtags(raw: str) -> list[str]:
    """"#CaveMen, sabunpria  #glowup" -> ['cavemen', 'sabunpria', 'glowup'].

    Dibuat memaafkan karena isinya ditempel dari brief brand: pemisahnya bisa
    koma, spasi, atau baris baru, dan tanda pagarnya boleh ada boleh tidak.
    """
    tokens = (raw or "").replace("\n", " ").replace(",", " ").split()
    return [tag for tag in (_slug(token) for token in tokens) if tag]


def leading_hashtags(campaign: dict | None = None) -> list[str]:
    """Hashtag yang selalu ikut di depan, sesuai jenis clip-nya.

    Di mode campaign, brand-lah yang menentukan tag wajibnya — biasanya ada tag
    tracking yang jadi syarat pembayaran, jadi tag itu tidak boleh kalah tempat
    oleh tag identitas channel. Kalau brand tidak memberi daftar, dipakai
    cadangan yang setidaknya tidak salah kategori.
    """
    campaign = campaign or {}
    if campaign.get("hashtags"):
        return list(campaign["hashtags"])
    if campaign.get("product"):
        return CLIP_CAMPAIGN_HASHTAGS
    return CLIP_FIXED_HASHTAGS


def build_hashtags(cand: dict, campaign: dict | None = None) -> list[str]:
    """Gabungkan hashtag tetap, nama tokoh, lalu hashtag dari Gemini.

    Urutannya disengaja: yang tetap di depan supaya identitas channel (atau
    syarat brand) konsisten, nama tokoh berikutnya karena itu yang paling dicari
    orang, sisanya menyusul. Duplikat dibuang tanpa memandang huruf besar-kecil
    — Gemini sering menghasilkan tag yang sama dengan yang sudah tetap.
    """
    ordered: list[str] = []
    seen: set[str] = set()

    def add(tag: str) -> None:
        clean = _slug(tag)
        if clean and clean not in seen:
            seen.add(clean)
            ordered.append(clean)

    for tag in leading_hashtags(campaign):
        add(tag)
    for person in (cand.get("people") or []):
        add(person)
    for tag in (cand.get("hashtags") or []):
        add(tag)

    return ordered


def _hashtag_line(hashtags) -> str:
    if not hashtags:
        return ""
    return " ".join(f"#{tag.lstrip('#')}" for tag in hashtags)


def build_attribution(video_title: str, product: str = "", channel: str = "") -> str:
    """Blok atribusi yang sesuai jenis clip-nya.

    Blok reguler menyatakan hak cipta milik pemilik konten asli — pernyataan yang
    keliru untuk clip campaign, yang justru dibuat atas permintaan pemilik
    produknya. Maka begitu ada nama produk, bloknya diganti.
    """
    if product:
        return CLIP_CAMPAIGN_ATTRIBUTION.format(source=video_title, product=product)
    return CLIP_ATTRIBUTION.format(source=video_title, channel=channel)


def build_caption(
    cand: dict, video_title: str, campaign: dict | None = None
) -> str:
    """Caption siap tempel: deskripsi, atribusi, lalu hashtag.

    Inilah satu-satunya tempat caption dirakit, dipakai bersama oleh kartu clip
    dan berkas analisis — supaya keduanya tidak pernah berbeda isi.
    """
    campaign = campaign or {}
    blocks = []

    description = (cand.get("description") or "").strip()
    if description:
        blocks.append(description)

    blocks.append(build_attribution(video_title, campaign.get("product", "")))

    tag_line = _hashtag_line(build_hashtags(cand, campaign))
    if tag_line:
        blocks.append(tag_line)

    return "\n\n".join(blocks)


def build_report(
    cand: dict,
    video_title: str,
    source_label: str = "",
    index: int | None = None,
    campaign: dict | None = None,
) -> str:
    """Susun seluruh analisis sebuah kandidat jadi markdown siap baca."""
    parts = cand.get("parts") or [
        {
            "start": cand.get("start"),
            "end": cand.get("end"),
            "duration_seconds": cand.get("duration_seconds"),
        }
    ]

    lines = [f"# {cand.get('title') or 'Clip tanpa judul'}", ""]

    heading = f"Dari: {video_title}"
    if index is not None:
        heading = f"Clip #{index} — " + heading
    lines.append(heading)
    if source_label:
        lines.append(f"Sumber: {source_label}")
    lines.append("")

    lines.append("## Potongan")
    lines.append(
        f"{len(parts)} potongan · total "
        f"{seconds_to_timestamp(cand.get('duration_seconds') or 0)}"
    )
    for number, part in enumerate(parts, 1):
        lines.append(
            f"- Potongan {number}: {part.get('start')} → {part.get('end')} "
            f"({int(part.get('duration_seconds') or 0)} detik)"
        )
        if number < len(parts):
            gap = (
                parts[number].get("start_seconds", 0)
                - part.get("start_seconds", 0)
                - (part.get("duration_seconds") or 0)
            )
            if gap > 0:
                lines.append(f"  - dibuang {int(gap)} detik di sambungan ini")
    lines.append("")

    if cand.get("hook_start") and cand.get("hook_end"):
        placement = (
            "pindahkan ke paling depan saat edit"
            if cand.get("hook_placement") == "pindahkan_ke_awal"
            else "sudah jadi pembuka clip"
        )
        lines += [
            "## Hook",
            f"{cand['hook_start']} → {cand['hook_end']} — {placement}",
            "",
        ]
    if cand.get("hook"):
        lines += ["> " + cand["hook"], ""]

    if cand.get("campaign_fit") is not None:
        lines += [
            "## Campaign",
            f"Kecocokan produk: **{cand['campaign_fit']}/10**"
            + (f" · produk muncul di {cand['product_moment']}"
               if cand.get("product_moment") else ""),
            "",
        ]

    lines += ["## Kenapa clip ini dipilih", cand.get("reason") or "-", ""]

    notes = cand.get("signal_notes") or {}
    if notes:
        lines.append("### Pembacaan tiap sinyal")
        for signal, note in notes.items():
            lines.append(f"- **{signal.capitalize()}:** {note}")
        lines.append("")

    scores = cand.get("segment_scores") or {}
    if scores:
        readings = [
            f"{label} {'n/a' if scores.get(key) is None else f'{scores[key]:.2f}'}"
            for label, key in (
                ("Comment", "score_comment"), ("Audio", "score_audio"),
                ("Narrative", "score_narrative"), ("Text", "score_text"),
            )
        ]
        lines += ["### Skor prefilter", " · ".join(readings), ""]

    lines += [
        "## Metadata upload",
        f"**Tipe:** {cand.get('clip_type') or '-'} · "
        f"**Skor:** {cand.get('final_score') or '-'}/10 · "
        f"**Layak dibagikan:** {cand.get('shareability') or '-'}/10 · "
        f"**Memancing komentar:** {cand.get('discussion_trigger') or '-'}/10",
        "",
        "**Title**", "```", cand.get("title") or "", "```", "",
        "**Description**", "```", cand.get("description") or "", "```", "",
    ]

    tag_line = _hashtag_line(build_hashtags(cand, campaign))
    if tag_line:
        lines += ["**Hashtags**", "```", tag_line, "```", ""]

    lines += [
        "**Caption lengkap — siap tempel**",
        "```", build_caption(cand, video_title, campaign), "```", "",
    ]

    if cand.get("thumbnail_prompt"):
        lines += ["## Thumbnail", ""]
        if cand.get("thumbnail_text"):
            lines += ["**Teks di gambar**", "```", cand["thumbnail_text"], "```", ""]
        lines += [
            "**Prompt untuk AI gambar** "
            "(lampirkan screenshot pembicara dari clip ini)",
            "```", cand["thumbnail_prompt"], "```", "",
        ]

    return "\n".join(lines)


def write_report(video_path: str, content: str) -> str:
    """Tulis laporan berdampingan dengan videonya. Return path berkasnya."""
    report_path = os.path.splitext(video_path)[0] + ".md"
    with open(report_path, "w", encoding="utf-8") as handle:
        handle.write(content)
    return report_path
