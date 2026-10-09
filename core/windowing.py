"""Bangun window kandidat dari jeda hening di transcript.

Menggantikan `segment_transcript()` window-tetap dari v1.1. Alasannya: clip yang
mulai di tengah kalimat sudah gagal sebelum dinilai. Memotong di jeda natural
menyerang kriteria self-contained di akarnya, bukan menambalnya dengan penalti.

Window di sini adalah LINGKUNGAN kandidat, bukan batas clip final. Batas final
ditentukan Gemini dari konteks yang disediakan `get_context_segments()`.
"""

import bisect
from utils.constants import (
    PAUSE_GAP_THRESHOLD,
    PAUSE_SNAP_TOLERANCE,
    MAX_WINDOW_SECONDS,
    MIN_WINDOW_SECONDS,
)

EPS = 1e-4  # Toleransi presisi float


def _split_points_from_pauses(
    pauses: list[float],
    whisper_segments: list[dict],
    tolerance: float = PAUSE_SNAP_TOLERANCE,
) -> set[int]:
    """Ubah waktu jeda jadi indeks segmen tempat window boleh dipecah (Optimasi O(M log N))."""
    if not pauses or len(whisper_segments) < 2:
        return set()

    starts = [s["start"] for s in whisper_segments[1:]]
    points: set[int] = set()

    for pause in pauses:
        idx = bisect.bisect_left(starts, pause)
        candidates = []
        if idx < len(starts):
            candidates.append(idx)
        if idx > 0:
            candidates.append(idx - 1)

        best_idx = None
        min_diff = float("inf")
        for c in candidates:
            diff = abs(starts[c] - pause)
            if diff < min_diff:
                min_diff = diff
                best_idx = c

        if best_idx is not None and min_diff <= tolerance:
            # +1 karena starts di-offset dari indeks 1 whisper_segments
            points.add(best_idx + 1)

    return points


def _build_window(indices: list[int], whisper_segments: list[dict]) -> dict:
    segs = [whisper_segments[i] for i in indices]
    text = " ".join(s["text"].strip() for s in segs).strip()
    start = segs[0]["start"]
    end = segs[-1]["end"]
    return {
        "start_seconds": start,
        "end_seconds": end,
        "duration_seconds": end - start,
        "transcript_text": text,
        "word_count": len(text.split()),
        "segment_indices": indices,
    }


def _best_split_index(group: list[int], whisper_segments: list[dict]) -> int | None:
    """Cari titik pecah paling natural di dalam group: jeda terbesar antar segmen.

    Mendukung gap 0.0 dengan memilih titik potong yang paling seimbang (mendekati tengah).
    """
    if len(group) < 2:
        return None

    start_time = whisper_segments[group[0]]["start"]
    best_pos = None
    best_gap = -1.0
    best_distance_from_mid = float("inf")
    mid_pos = len(group) / 2.0

    for pos in range(1, len(group)):
        prev = whisper_segments[group[pos - 1]]
        cur = whisper_segments[group[pos]]

        if cur["start"] - start_time < MIN_WINDOW_SECONDS:
            continue

        gap = cur["start"] - prev["end"]
        dist_from_mid = abs(pos - mid_pos)

        # Pilih gap terbesar; jika gap sama (misal sama-sama 0), pilih yang paling dekat dengan tengah
        if gap > best_gap or (abs(gap - best_gap) < EPS and dist_from_mid < best_distance_from_mid):
            best_gap = gap
            best_pos = pos
            best_distance_from_mid = dist_from_mid

    return best_pos


def build_windows(
    whisper_segments: list[dict],
    pauses: list[float] | None = None,
) -> list[dict]:
    """Gabungkan segmen whisper jadi window natural berbasis jeda bicara."""
    if not whisper_segments:
        return []

    pause_points = _split_points_from_pauses(pauses or [], whisper_segments)

    # Tahap 1: pecah berdasarkan jeda dan durasi maksimum
    groups: list[list[int]] = []
    current = [0]

    for i in range(1, len(whisper_segments)):
        prev = whisper_segments[i - 1]
        seg = whisper_segments[i]

        gap = seg["start"] - prev["end"]
        window_duration = seg["end"] - whisper_segments[current[0]]["start"]
        at_pause = i in pause_points

        if at_pause or gap > PAUSE_GAP_THRESHOLD:
            groups.append(current)
            current = [i]
        elif window_duration > MAX_WINDOW_SECONDS:
            split_pos = _best_split_index(current, whisper_segments)
            if split_pos is not None:
                groups.append(current[:split_pos])
                current = current[split_pos:] + [i]
            else:
                groups.append(current)
                current = [i]
        else:
            current.append(i)

    if current:
        groups.append(current)

    # Tahap 2: tempel window kependekan (2-pass: backward & forward)
    merged: list[list[int]] = []
    for group in groups:
        segs = [whisper_segments[i] for i in group]
        duration = segs[-1]["end"] - segs[0]["start"]

        if duration < MIN_WINDOW_SECONDS and merged:
            head = whisper_segments[merged[-1][0]]["start"]
            if segs[-1]["end"] - head <= MAX_WINDOW_SECONDS:
                merged[-1].extend(group)
                continue

        merged.append(group)

    # Pass tambahan: Jika grup pertama masih < MIN_WINDOW_SECONDS, gabungkan ke grup setelahnya
    if len(merged) > 1:
        first_segs = [whisper_segments[i] for i in merged[0]]
        first_duration = first_segs[-1]["end"] - first_segs[0]["start"]
        if first_duration < MIN_WINDOW_SECONDS:
            second_segs = [whisper_segments[i] for i in merged[1]]
            if second_segs[-1]["end"] - first_segs[0]["start"] <= MAX_WINDOW_SECONDS:
                merged[1] = merged[0] + merged[1]
                merged.pop(0)

    return [_build_window(group, whisper_segments) for group in merged]


def get_context_segments(
    window: dict, whisper_segments: list[dict], context_seconds: int
) -> dict:
    """Ambil segmen sebelum, di dalam, dan sesudah window."""
    win_start = window["start_seconds"]
    win_end = window["end_seconds"]
    ctx_start = win_start - context_seconds
    ctx_end = win_end + context_seconds

    before = [
        s for s in whisper_segments
        if s["end"] > ctx_start + EPS and s["end"] <= win_start + EPS
    ]
    core = [
        s for s in whisper_segments
        if s["start"] >= win_start - EPS and s["end"] <= win_end + EPS
    ]
    after = [
        s for s in whisper_segments
        if s["start"] >= win_end - EPS and s["start"] < ctx_end - EPS
    ]

    return {"before": before, "core": core, "after": after}