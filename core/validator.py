"""Validasi kandidat dari Gemini.

LLM bisa mengarang timestamp. Tanpa lapisan ini, halusinasi langsung jadi
potongan video yang salah — user klik Download dan dapat detik yang keliru.

Empat tahap berurutan. Urutannya wajib: snap harus pertama karena semua tahap
setelahnya bergantung pada timestamp yang sudah benar.
"""

import logging

from utils.constants import (
    SNAP_TOLERANCE,
    MIN_CLIP_SECONDS,
    MAX_CLIP_SECONDS,
    DEDUPE_MIN_GAP,
    MAX_TYPE_RATIO,
    MAX_CLIP_PARTS,
    MIN_PART_SECONDS,
    CAMPAIGN_MIN_FIT,
    QUALITY_MIN_STANDALONE,
)
from utils.helpers import timestamp_to_seconds, seconds_to_timestamp

logger = logging.getLogger(__name__)

# Toleransi pembandingan float.
EPS = 0.01


def _floor(value: float, options: list[float]) -> float:
    """Batas terbesar yang tidak melewati `value` (Fallback untuk segmen kasar)."""
    below = [o for o in options if o <= value + EPS]
    return max(below) if below else min(options)


def _ceil(value: float, options: list[float]) -> float:
    """Batas terkecil yang tidak kurang dari `value` (Fallback untuk segmen kasar)."""
    above = [o for o in options if o >= value - EPS]
    return min(above) if above else max(options)


def _raw_parts(cand: dict) -> list[dict] | None:
    """Ambil daftar bagian, terima juga bentuk lama start/end tunggal."""
    parts = cand.get("parts")
    if parts:
        return parts
    if cand.get("start") is not None and cand.get("end") is not None:
        return [{"start": cand["start"], "end": cand["end"]}]
    return None


def _snap_to_nearest_word(
    target_seconds: float,
    words: list[dict],
    tolerance: float = SNAP_TOLERANCE,
    snap_key: str = "start",
) -> float | None:
    """Cari timestamp kata terdekat dengan target_seconds dalam batas tolerance."""
    if not words:
        return None

    best_word_time = None
    min_diff = float("inf")

    for w in words:
        w_time = w.get(snap_key)
        if w_time is None:
            continue
        diff = abs(w_time - target_seconds)
        if diff < min_diff:
            min_diff = diff
            best_word_time = w_time

    if min_diff <= tolerance and best_word_time is not None:
        return best_word_time

    return None


def snap_to_transcript(
    candidates: list[dict],
    words_or_segments: list[dict],
    tolerance: float = SNAP_TOLERANCE,
    max_seconds: int = MAX_CLIP_SECONDS,
) -> tuple[list[dict], int]:
    """Cocokkan timestamp Gemini ke batas kata terdekat (Word-Level Snap)."""
    if not words_or_segments:
        return [], len(candidates)

    # Ekstraksi semua kata jika input bertingkat segmen
    all_words = []
    if words_or_segments and "word" in words_or_segments[0]:
        all_words = words_or_segments
    else:
        for seg in words_or_segments:
            if "words" in seg and isinstance(seg["words"], list):
                all_words.extend(seg["words"])

    use_word_level = len(all_words) > 0

    valid = []
    for cand in candidates:
        raw_parts = _raw_parts(cand)
        if not raw_parts or len(raw_parts) > MAX_CLIP_PARTS:
            continue

        snapped: list[dict] = []
        ok = True

        for part in raw_parts:
            try:
                raw_start = timestamp_to_seconds(part["start"])
                raw_end = timestamp_to_seconds(part["end"])
            except (KeyError, ValueError, TypeError):
                ok = False
                break

            if use_word_level:
                # Snap presisi ke tingkat kata (start -> w.start, end -> w.end)
                part_start = _snap_to_nearest_word(
                    raw_start, all_words, tolerance=tolerance, snap_key="start"
                )
                part_end = _snap_to_nearest_word(
                    raw_end, all_words, tolerance=tolerance, snap_key="end"
                )

                if part_start is None or part_end is None:
                    ok = False
                    break
            else:
                # Fallback ke segmen kasar jika data kata tidak tersedia
                starts = [s["start"] for s in words_or_segments if "start" in s]
                ends = [s["end"] for s in words_or_segments if "end" in s]
                if not starts or not ends:
                    ok = False
                    break

                part_start = _floor(raw_start, starts)
                part_end = _ceil(raw_end, ends)

            if part_end <= part_start or (part_end - part_start) < MIN_PART_SECONDS:
                ok = False
                break

            # Cegah tumpang tindih antar bagian pada clip multi-part
            if snapped and part_start < snapped[-1]["end_seconds"]:
                ok = False
                break

            snapped.append({
                "start_seconds": part_start,
                "end_seconds": part_end,
                "start": seconds_to_timestamp(part_start),
                "end": seconds_to_timestamp(part_end),
                "duration_seconds": part_end - part_start,
            })

        if not ok or not snapped:
            continue

        cand["parts"] = snapped
        cand["start_seconds"] = snapped[0]["start_seconds"]
        cand["end_seconds"] = snapped[-1]["end_seconds"]
        cand["start"] = snapped[0]["start"]
        cand["end"] = snapped[-1]["end"]
        cand["duration_seconds"] = sum(p["duration_seconds"] for p in snapped)
        valid.append(cand)

    return valid, len(candidates) - len(valid)


def normalize_hook(candidates: list[dict]) -> int:
    """Pastikan titik hook benar-benar berada di dalam clip. Return jumlah yang dibuang."""
    dropped = 0

    for cand in candidates:
        raw_start = cand.get("hook_start")
        raw_end = cand.get("hook_end")
        if not raw_start or not raw_end:
            continue

        try:
            hook_start = timestamp_to_seconds(raw_start)
            hook_end = timestamp_to_seconds(raw_end)
        except (ValueError, AttributeError, TypeError):
            cand["hook_start"] = cand["hook_end"] = None
            dropped += 1
            continue

        parts = cand.get("parts") or []
        inside = any(
            part["start_seconds"] - EPS <= hook_start
            and hook_end <= part["end_seconds"] + EPS
            for part in parts
        )

        if not inside or hook_end <= hook_start:
            cand["hook_start"] = cand["hook_end"] = None
            cand["hook_placement"] = None
            dropped += 1
            continue

        cand["hook_seconds"] = round(hook_end - hook_start, 2)

    return dropped


def filter_by_duration(
    candidates: list[dict],
    min_seconds: int = MIN_CLIP_SECONDS,
    max_seconds: int = MAX_CLIP_SECONDS,
) -> tuple[list[dict], int]:
    """Hard filter durasi. Dijalankan SETELAH snap karena snap bisa menggeser timestamp."""
    kept = [
        c for c in candidates
        if min_seconds <= c.get("duration_seconds", 0) <= max_seconds
    ]
    return kept, len(candidates) - len(kept)


def _compute_parts_overlap(parts1: list[dict], parts2: list[dict]) -> float:
    """Hitung total irisan durasi antar bagian (parts) presisi."""
    total_overlap = 0.0
    for p1 in parts1:
        s1, e1 = p1["start_seconds"], p1["end_seconds"]
        for p2 in parts2:
            s2, e2 = p2["start_seconds"], p2["end_seconds"]
            overlap = max(0.0, min(e1, e2) - max(s1, s2))
            total_overlap += overlap
    return total_overlap


def dedupe(
    candidates: list[dict], max_overlap_ratio: float = 0.5
) -> tuple[list[dict], int]:
    """Buang kandidat yang tumpang tindih berlebihan (>50% irisan). Skor tertinggi menang."""
    ordered = sorted(
        candidates, key=lambda c: c.get("final_score", 0), reverse=True
    )

    kept = []
    for cand in ordered:
        c_parts = cand.get("parts", [])
        c_dur = max(cand.get("duration_seconds", 0), 1e-6)

        duplicate = False
        for k in kept:
            k_parts = k.get("parts", [])
            overlap = _compute_parts_overlap(c_parts, k_parts)

            if (overlap / c_dur) > max_overlap_ratio:
                duplicate = True
                break

        if not duplicate:
            kept.append(cand)

    return kept, len(candidates) - len(kept)


def enforce_type_diversity(
    candidates: list[dict], max_ratio: float = MAX_TYPE_RATIO
) -> tuple[list[dict], int]:
    """Batasi proporsi tiap clip_type di hasil final."""
    if not candidates:
        return [], 0

    max_per_type = max(1, int(len(candidates) * max_ratio))

    ordered = sorted(
        candidates, key=lambda c: c.get("final_score", 0), reverse=True
    )

    counts: dict[str, int] = {}
    kept = []
    for cand in ordered:
        clip_type = cand.get("clip_type") or "unknown"
        if counts.get(clip_type, 0) >= max_per_type:
            continue
        counts[clip_type] = counts.get(clip_type, 0) + 1
        kept.append(cand)

    return kept, len(candidates) - len(kept)


def attach_segment_scores(
    candidates: list[dict], windows: list[dict]
) -> list[dict]:
    """Tempelkan skor sinyal dari window yang paling banyak beririsan."""
    for cand in candidates:
        start = cand["start_seconds"]
        end = cand["end_seconds"]

        best, best_overlap = None, 0.0
        for window in windows:
            overlap = min(end, window["end_seconds"]) - max(start, window["start_seconds"])
            if overlap > best_overlap:
                best, best_overlap = window, overlap

        cand["segment_scores"] = {
            "score_comment": best.get("score_comment") if best else None,
            "score_audio": best.get("score_audio") if best else None,
            "score_narrative": best.get("score_narrative") if best else None,
            "score_text": best.get("score_text") if best else None,
        } if best else {}

    return candidates


def normalize_scores(candidates: list[dict]) -> tuple[list[dict], bool]:
    """Paksa `final_score` ke skala 0–10."""
    scores = [c.get("final_score") for c in candidates if c.get("final_score") is not None]
    if not scores or max(scores) > 1.0:
        return candidates, False

    for cand in candidates:
        if cand.get("final_score") is not None:
            cand["final_score"] = round(cand["final_score"] * 10, 1)

    logger.warning("final_score terdeteksi berskala 0-1, dikalikan 10")
    return candidates, True


def filter_by_campaign_fit(
    candidates: list[dict], min_fit: float = CAMPAIGN_MIN_FIT
) -> tuple[list[dict], int]:
    """Buang kandidat yang produknya tidak ada."""
    kept, dropped = [], 0
    for cand in candidates:
        fit = cand.get("campaign_fit")
        if fit is None or fit < min_fit or not (cand.get("product_moment") or "").strip():
            dropped += 1
            continue
        kept.append(cand)
    return kept, dropped


def _campaign_rank_score(cand: dict) -> float:
    """Clip campaign dinilai dari elemen terlemahnya."""
    return min(cand.get("final_score", 0) or 0, cand.get("campaign_fit", 0) or 0)


def filter_by_quality(
    candidates: list[dict], min_standalone: float = QUALITY_MIN_STANDALONE
) -> tuple[list[dict], int]:
    """Gerbang mutu: buang clip yang tidak bisa dipahami sendiri."""
    kept, dropped = [], 0
    for cand in candidates:
        score = cand.get("standalone_context")
        if score is not None and score < min_standalone:
            dropped += 1
            continue
        kept.append(cand)
    return kept, dropped


def _rank_score(cand: dict) -> float:
    """Peringkat viral, dibatasi oleh payoff_strength."""
    final = cand.get("final_score", 0) or 0
    payoff = cand.get("payoff_strength")
    if payoff is None:
        return final
    return min(final, payoff)


def validate(
    candidates: list[dict],
    words_or_segments: list[dict],
    windows: list[dict] | None = None,
    min_seconds: int = MIN_CLIP_SECONDS,
    max_seconds: int = MAX_CLIP_SECONDS,
    campaign_mode: bool = False,
) -> tuple[list[dict], dict]:
    """Jalankan keempat tahap validasi berurutan dan kumpulkan statistiknya."""
    received = len(candidates)

    candidates, rescaled = normalize_scores(candidates)
    
    # 1. Snap menggunakan word timestamps (atau fallback ke segmen)
    candidates, dropped_snap = snap_to_transcript(
        candidates, words_or_segments, max_seconds=max_seconds
    )
    
    # 2. Hard filter durasi
    candidates, dropped_duration = filter_by_duration(
        candidates, min_seconds, max_seconds
    )
    
    # 3. Deduplikasi berbasis rasio overlap (IoU > 50%)
    candidates, dropped_dedupe = dedupe(candidates)

    # 4. Filter kualitas kontekstual
    candidates, dropped_quality = filter_by_quality(candidates)

    dropped_diversity = 0
    if not campaign_mode:
        candidates, dropped_diversity = enforce_type_diversity(candidates)

    dropped_campaign = 0
    if campaign_mode:
        candidates, dropped_campaign = filter_by_campaign_fit(candidates)

    dropped_hook = normalize_hook(candidates)

    rank_key = _campaign_rank_score if campaign_mode else _rank_score
    candidates.sort(key=rank_key, reverse=True)
    for rank, cand in enumerate(candidates, 1):
        cand["rank"] = rank

    if windows:
        candidates = attach_segment_scores(candidates, windows)

    stats = {
        "received": received,
        "dropped_snap": dropped_snap,
        "dropped_duration": dropped_duration,
        "dropped_dedupe": dropped_dedupe,
        "dropped_quality": dropped_quality,
        "dropped_diversity": dropped_diversity,
        "dropped_campaign": dropped_campaign,
        "dropped_hook": dropped_hook,
        "final": len(candidates),
        "score_rescaled": rescaled,
    }

    return candidates, stats