"""Signal 4 (bobot 0.20) plus disqualifier iklan/boilerplate.

Sengaja dijaga dangkal. Segala usaha menilai "kekuatan hook" lewat keyword
matching sudah dihapus dari sini — kalimat "Ini rahasia yang gila banget" akan
menang atas "Saya baru sadar ayah saya tidak pernah tahu", padahal yang kedua
hook-nya jauh lebih kuat. Penilaian itu pekerjaan Gemini.
"""

import re
from typing import Sequence

from utils.constants import AD_PATTERNS, BOILERPLATE_PATTERNS

DENSITY_HIGH = 2.5
DENSITY_MID = 1.5

HAS_DIGIT_RE = re.compile(r"\d")


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _find_disqualifier(text: str, allow_ads: bool = False) -> str | None:
    """Pola iklan/boilerplate pertama yang cocok, kalau ada."""
    lowered = text.lower()
    
    # Menghindari TypeError akibat kombinasi tuple/list/set yang berbeda
    patterns: list[str] = list(BOILERPLATE_PATTERNS)
    if not allow_ads:
        patterns.extend(AD_PATTERNS)

    for pattern in patterns:
        if pattern in lowered:
            return pattern
    return None


def score_windows(windows: list[dict], allow_ads: bool = False) -> list[dict]:
    """Beri skor teks dan tandai window yang harus dibuang.

    Disqualifier adalah HARD DROP, bukan penalti skor: iklan sponsor selalu
    antusias, cepat, dan penuh superlatif, jadi dia lolos setiap sinyal
    intensitas dengan skor tinggi. Penalti kecil tidak cukup — dia harus
    dikeluarkan sepenuhnya dari kandidat.

    `allow_ads` membalik aturan itu untuk analisis campaign: di sana segmen yang
    menyebut produk justru satu-satunya bahan yang berguna. Tanpa saklar ini,
    mode campaign akan kelaparan bahan secara DIAM-DIAM — window produknya
    dibuang di prefilter, jauh sebelum Gemini sempat melihatnya. Boilerplate
    intro/outro tetap dibuang: itu tidak pernah jadi bahan clip.
    """
    for window in windows:
        text = window.get("transcript_text") or ""
        word_count = window.get("word_count")
        if word_count is None:
            word_count = len(text.split())

        duration = window.get("duration_seconds") or 0.0
        # Mencegah pembagian nol dan mengamankan perhitungan density
        density = (word_count / duration) if duration > 0 else 0.0

        if density > DENSITY_HIGH:
            density_score = 1.0
        elif density > DENSITY_MID:
            density_score = 0.7
        else:
            density_score = 0.4

        window["score_text"] = round(
            _clamp(
                density_score * 0.55
                + (0.20 if HAS_DIGIT_RE.search(text) else 0.0)
                + (0.15 if "?" in text else 0.0)
                + (0.10 if "!" in text else 0.0)
            ),
            4,
        )

        reason = _find_disqualifier(text, allow_ads)
        window["is_disqualified"] = reason is not None
        window["disqualify_reason"] = reason

    return windows