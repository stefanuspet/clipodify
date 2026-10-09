"""Signal 1 (bobot 0.30) — mention timestamp di komentar penonton.

Satu-satunya sinyal yang bukan proxy. Ketika 40 orang menulis "12:34 😂", itu
bukan tebakan soal apa yang mungkin menarik — itu bukti orang benar-benar
bereaksi. Semua sinyal lain menerka apa yang sinyal ini ukur langsung.

Keterbatasannya jujur: hanya berguna untuk video yang sudah punya komentar.
Untuk video baru, `score_comment` bernilai None dan fusion menormalisasi ulang
bobotnya ke sinyal lain.
"""

import re

from utils.constants import COMMENT_PAD_SECONDS, MIN_MENTIONS_FOR_SIGNAL

# Cocokkan "12:34" dan "1:02:15"
TIMESTAMP_RE = re.compile(r"\b(?:(\d{1,2}):)?(\d{1,2}):(\d{2})\b")

# Bonus kalau window bertepatan dengan chapter marker dari creator
CHAPTER_BONUS = 0.15


def extract_timestamp_mentions(comments: list[dict]) -> list[tuple[int, int]]:
    """Ambil semua mention timestamp dari komentar.

    Return: [(detik, like_count)]. Satu komentar boleh menghasilkan beberapa
    mention — komentar "best bits: 3:20, 14:02" menghitung dua-duanya.
    """
    mentions = []

    for comment in comments or []:
        text = comment.get("text") or ""
        likes = comment.get("like_count") or 0

        for match in TIMESTAMP_RE.finditer(text):
            hours, minutes, seconds = match.groups()
            total = (int(hours) if hours else 0) * 3600 \
                + int(minutes) * 60 + int(seconds)
            mentions.append((total, likes))

    return mentions


def chapter_for_window(chapters: list[dict], window: dict) -> str | None:
    """Judul chapter yang memuat titik tengah window, kalau ada."""
    mid = (window["start_seconds"] + window["end_seconds"]) / 2

    for chapter in chapters or []:
        if chapter.get("start_time", 0) <= mid <= chapter.get("end_time", 0):
            return chapter.get("title") or None

    return None


def score_windows(
    windows: list[dict],
    mentions: list[tuple[int, int]],
    chapters: list[dict],
) -> list[dict]:
    """Beri skor comment ke tiap window.

    Kalau tidak ada mention sama sekali, `score_comment` diisi None — BUKAN 0.0.
    Bedanya penting: "tidak ada data" bukan "tidak ada yang menyebut". None
    inilah yang memicu normalisasi ulang bobot di fusion.
    """
    if not windows:
        return windows

    # Terlalu sedikit mention = tidak ada sinyal, bukan sinyal lemah.
    # Menormalisasi 2 mention terhadap window terramai memberi satu window skor
    # 1.0 dan sisanya 0.0 — pembelahan biner dari dua data poin, yang lalu
    # dikalikan bobot penuh 0.30. Lebih jujur menyatakan datanya tidak cukup.
    if len(mentions) < MIN_MENTIONS_FOR_SIGNAL:
        for window in windows:
            window["score_comment"] = None
            window["comment_features"] = {
                "mention_count": 0,
                "weighted_mentions": 0.0,
                "chapter": chapter_for_window(chapters, window),
                "below_threshold": len(mentions) > 0,
            }
        return windows

    # Kumpulkan mention per window
    for window in windows:
        low = window["start_seconds"] - COMMENT_PAD_SECONDS
        high = window["end_seconds"] + COMMENT_PAD_SECONDS

        matched = [(t, likes) for t, likes in mentions if low <= t <= high]
        window["comment_features"] = {
            "mention_count": len(matched),
            "weighted_mentions": float(sum(1 + likes for _, likes in matched)),
            "chapter": chapter_for_window(chapters, window),
        }

    # Normalisasi RELATIF terhadap window paling ramai di video ini.
    # Video 2 juta view dan video 5 ribu view tidak boleh diukur dengan skala
    # absolut yang sama — yang penting menit mana yang paling diperbincangkan
    # di video ini, bukan berapa angka mutlaknya.
    max_weighted = max(
        w["comment_features"]["weighted_mentions"] for w in windows
    ) or 1.0

    for window in windows:
        score = window["comment_features"]["weighted_mentions"] / max_weighted
        if window["comment_features"]["chapter"]:
            score += CHAPTER_BONUS
        window["score_comment"] = min(score, 1.0)

    return windows
