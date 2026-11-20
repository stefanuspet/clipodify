"""Pilih posisi Y subtitle dan teks supaya tidak menutupi wajah di kanvas 1080×1920."""

from __future__ import annotations

from dataclasses import dataclass

CANVAS_W, CANVAS_H = 1080, 1920
_SAFE_TOP, _SAFE_BOTTOM = 110, 1850
_PAD = 10

PREFERRED_Y = {"top": 250, "center": 960, "bottom": 1560}


@dataclass
class ShotZones:
    t_start: float
    t_end: float
    faces: list[tuple[int, int, int, int]]
    seams: list[int]


def shot_zones(ctx: dict) -> list[ShotZones]:
    from backend.analyzer.grid_composer import layout_seams, output_face_zones
    from backend.editing.context import shots_to_segments

    out = []
    for sg in shots_to_segments(ctx["shots"]):
        faces = []
        for x0, y0, x1, y1 in output_face_zones(sg, ctx["width"], ctx["height"]):
            fw, fh = x1 - x0, y1 - y0
            # Lebarkan ke seluruh kepala: rambut/hijab di atas, dagu di bawah
            faces.append((int(x0 - fw * 0.25), int(y0 - fh * 0.45),
                          int(x1 + fw * 0.25), int(y1 + fh * 0.30)))
        out.append(ShotZones(sg.t_start, sg.t_end, faces, layout_seams(sg.layout, len(sg.persons))))
    return out


def _overlap(a, b) -> int:
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    return w * h if w > 0 and h > 0 else 0


def choose_y(
    zones: list[ShotZones],
    start: float,
    end: float,
    height: int,
    width: int,
    preferred: int,
    prev_y: int | None = None,
    avoid: list[tuple[int, int, int, int]] | None = None,
    cx: int | None = None,
) -> int:
    """Pusat Y untuk sebuah kotak teks yang tampil di [start, end).

    Urutan coba: posisi sebelumnya (kalau masih aman, supaya tidak lompat),
    posisi pilihan, garis sambungan antar cell, lalu grid bertahap menjauh dari
    posisi pilihan. Kalau tidak ada yang bersih, ambil yang tumpang-tindihnya terkecil.
    """
    active = [z for z in zones if z.t_start < end and z.t_end > start]
    blocks = [f for z in active for f in z.faces] + list(avoid or [])
    seams = sorted({y for z in active for y in z.seams}, key=lambda y: abs(y - preferred))
    width = min(width, CANVAS_W - 40)
    if cx is None:
        x0, x1 = (CANVAS_W - width) // 2, (CANVAS_W + width) // 2
    else:
        x0, x1 = cx - width // 2, cx + width // 2

    def box(y):
        return (x0, y - height // 2 - _PAD, x1, y + height // 2 + _PAD)

    def inside(y):
        return y - height // 2 >= _SAFE_TOP and y + height // 2 <= _SAFE_BOTTOM

    def cost(y):
        b = box(y)
        return sum(_overlap(b, f) for f in blocks)

    grid = sorted(range(_SAFE_TOP + height // 2, _SAFE_BOTTOM - height // 2 + 1, 20),
                  key=lambda y: abs(y - preferred))
    order = ([prev_y] if prev_y is not None else []) + [preferred] + seams + grid
    for y in order:
        if inside(y) and cost(y) == 0:
            return int(y)
    return int(min((y for y in order if inside(y)), key=cost, default=preferred))


def band(y: int, height: int, width: int) -> tuple[int, int, int, int]:
    width = min(width, CANVAS_W - 40)
    return ((CANVAS_W - width) // 2, y - height // 2, (CANVAS_W + width) // 2, y + height // 2)
