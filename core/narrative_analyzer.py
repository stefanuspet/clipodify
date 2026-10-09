"""Signal 3 (bobot 0.25) — struktur naratif tanya→jawab.

Satu-satunya sinyal yang mengukur BENTUK, bukan TINGKAT. Pasangan tanya→jawab
adalah unit naratif lengkap secara bawaan: tension dan resolusi sudah menempel
di strukturnya.

Yang ditangkapnya adalah justru yang dilewatkan semua sinyal intensitas —
jawaban tenang yang menghancurkan. Tidak ada tawa, tidak ada volume naik, tidak
ada kata heboh, dan sering jadi clip terbaik dalam satu episode.
"""

# Proporsi awal teks yang dianggap "awal" untuk deteksi pertanyaan
QUESTION_HEAD_RATIO = 0.2

# Batas bawah jendela deteksi. Tanpa ini, window pendek punya jendela beberapa
# kata saja dan pertanyaan pembuka yang jelas ikut terlewat: 20% dari 19 kata
# cuma 3 kata, sehingga "Do you love your wife?" tidak pernah terdeteksi.
MIN_QUESTION_HEAD_WORDS = 15

# Rentang ideal: pertanyaan pendek, jawaban substantif
ANSWER_RATIO_MIN = 0.6
ANSWER_RATIO_MAX = 0.9

SENTENCE_ENDINGS = (".", "!", "?")


def _analyze_text(text: str) -> dict:
    text = (text or "").strip()
    words = text.split()
    total = len(words)

    if total == 0:
        return {
            "has_question_early": False,
            "answer_ratio": 0.0,
            "complete_ending": False,
        }

    # Pastikan head_len tidak melebihi total kata yang ada
    head_len = min(total, max(MIN_QUESTION_HEAD_WORDS, int(total * QUESTION_HEAD_RATIO)))
    head_words = words[:head_len]
    head_text = " ".join(head_words)

    # Cari tanda tanya pertama yang benar-benar ada di dalam area head
    q_index_in_head = head_text.find("?")
    has_question_early = q_index_in_head != -1

    if has_question_early:
        # Hitung sisa kata setelah tanda tanya pertama di area head
        # Menggunakan posisi indeks karakter aktual di dalam 'text' asli
        actual_q_index = text.find("?")
        answer_text = text[actual_q_index + 1:].strip()
        answer_words = answer_text.split() if answer_text else []
        answer_ratio = len(answer_words) / total
    else:
        answer_ratio = 0.0

    return {
        "has_question_early": has_question_early,
        "answer_ratio": round(answer_ratio, 3),
        "complete_ending": text.endswith(SENTENCE_ENDINGS),
    }


def score_windows(windows: list[dict]) -> list[dict]:
    """Beri skor narrative ke tiap window.

    Window tanpa pola tanya→jawab tetap bisa lolos lewat sinyal lain — ini
    bonus, bukan syarat.
    """
    for window in windows:
        features = _analyze_text(window.get("transcript_text", ""))

        qa_score = 0.0
        if features["has_question_early"]:
            qa_score = (
                1.0
                if ANSWER_RATIO_MIN <= features["answer_ratio"] <= ANSWER_RATIO_MAX
                else 0.6
            )

        ending_score = 1.0 if features["complete_ending"] else 0.3

        window["score_narrative"] = round(
            qa_score * 0.65 + ending_score * 0.35, 4
        )
        window["narrative_features"] = features

    return windows