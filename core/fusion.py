"""Gabungkan keempat sinyal jadi satu skor, buang yang terdiskualifikasi.

Ingat prinsip P1: tugas lapisan ini bukan meranking dengan presisi, tapi
memastikan momen bagus tidak tersingkir sebelum Gemini melihatnya.
"""

from collections import Counter

from utils.constants import (
    WEIGHT_COMMENT,
    WEIGHT_AUDIO,
    WEIGHT_NARRATIVE,
    WEIGHT_TEXT,
    TOP_SEGMENTS,
)

NEUTRAL = 0.5


def compute_weights(windows: list[dict]) -> dict:
    """Bobot sinyal, dinormalisasi ulang kalau tidak ada data komentar.

    `score_comment` bernilai None (bukan 0.0) saat video tidak punya mention
    timestamp. Membiarkannya 0.0 akan menghukum setiap window secara merata
    dengan bobot 0.30 — merusak skala skor tanpa membedakan apa pun. Jadi
    bobotnya dibagi ulang ke tiga sinyal sisanya secara proporsional.
    """
    has_comments = any(w.get("score_comment") is not None for w in windows)

    if has_comments:
        return {
            "comment": WEIGHT_COMMENT,
            "audio": WEIGHT_AUDIO,
            "narrative": WEIGHT_NARRATIVE,
            "text": WEIGHT_TEXT,
        }

    total = WEIGHT_AUDIO + WEIGHT_NARRATIVE + WEIGHT_TEXT
    return {
        "comment": 0.0,
        "audio": WEIGHT_AUDIO / total,
        "narrative": WEIGHT_NARRATIVE / total,
        "text": WEIGHT_TEXT / total,
    }


def fuse(windows: list[dict]) -> tuple[list[dict], dict]:
    """Buang yang terdiskualifikasi, hitung skor final, ambil top N.

    Return: (top_windows, stats). `stats` ditampilkan di UI supaya kalau
    hasilnya jelek, terlihat di mana hilangnya.
    """
    total_windows = len(windows)

    disqualified = [w for w in windows if w.get("is_disqualified")]
    survivors = [w for w in windows if not w.get("is_disqualified")]

    weights = compute_weights(survivors or windows)

    for window in survivors:
        window["score_final"] = round(
            (window.get("score_comment") or 0.0) * weights["comment"]
            + (window.get("score_audio") if window.get("score_audio") is not None else NEUTRAL) * weights["audio"]
            + (window.get("score_narrative") if window.get("score_narrative") is not None else NEUTRAL) * weights["narrative"]
            + (window.get("score_text") if window.get("score_text") is not None else NEUTRAL) * weights["text"],
            4,
        )

    survivors.sort(key=lambda w: w["score_final"], reverse=True)

    stats = {
        "total_windows": total_windows,
        "disqualified": len(disqualified),
        "disqualify_reasons": dict(
            Counter(w.get("disqualify_reason") for w in disqualified)
        ),
        # 6 desimal, bukan 4: pembulatan 4 desimal membuat bobot ternormalisasi
        # berjumlah 0.9999 dan gagal pemeriksaan "total harus 1.0".
        "weights_used": {k: round(v, 6) for k, v in weights.items()},
        "has_comments": weights["comment"] > 0,
        "selected": min(len(survivors), TOP_SEGMENTS),
    }

    return survivors[:TOP_SEGMENTS], stats
