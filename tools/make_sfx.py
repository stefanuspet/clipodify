"""Buat SFX sintetis (bebas hak cipta, dibuat dari rumus) ke assets/sfx/.

Jalankan: python tools/make_sfx.py
"""

from __future__ import annotations

import os

import numpy as np
import soundfile as sf

SR = 44100
OUT = os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets", "sfx")


def _t(dur: float) -> np.ndarray:
    return np.arange(int(SR * dur)) / SR


def _norm(x: np.ndarray, peak_db: float = -3.0) -> np.ndarray:
    x = x / (np.max(np.abs(x)) + 1e-9)
    return x * 10 ** (peak_db / 20)


def _fade(x: np.ndarray, ms_in: float = 3, ms_out: float = 20) -> np.ndarray:
    n_in, n_out = int(SR * ms_in / 1000), int(SR * ms_out / 1000)
    x = x.copy()
    x[:n_in] *= np.linspace(0, 1, n_in)
    x[-n_out:] *= np.linspace(1, 0, n_out)
    return x


def _osc(freq: np.ndarray) -> np.ndarray:
    return np.sin(2 * np.pi * np.cumsum(freq) / SR)


def boing() -> np.ndarray:
    t = _t(0.55)
    f = 260 + 140 * np.sin(2 * np.pi * 11 * t) * np.exp(-5 * t)
    return _osc(f) * np.exp(-5.5 * t)


def bass_drop() -> np.ndarray:
    t = _t(1.3)
    f = 35 + 110 * np.exp(-4 * t)
    x = np.tanh(2.5 * _osc(f)) * np.exp(-2.2 * t)
    return x


def chime() -> np.ndarray:
    t = _t(1.2)
    x = np.zeros_like(t)
    for delay, f in ((0.0, 1318.5), (0.09, 1760.0)):
        tt = np.clip(t - delay, 0, None)
        env = np.where(t >= delay, np.exp(-4.0 * tt), 0)
        x += env * (np.sin(2 * np.pi * f * tt) + 0.35 * np.sin(2 * np.pi * 2.76 * f * tt))
    return x


def tick() -> np.ndarray:
    t = _t(0.07)
    rng = np.random.default_rng(7)
    noise = np.diff(rng.standard_normal(len(t) + 1))
    return (0.6 * noise + np.sin(2 * np.pi * 2600 * t)) * np.exp(-90 * t)


def swipe() -> np.ndarray:
    t = _t(0.42)
    rng = np.random.default_rng(11)
    noise = rng.standard_normal(len(t))
    cutoff = 300 + 6000 * (t / t[-1]) ** 1.5      # filter makin terbuka → "sweep" naik
    alpha = 1 - np.exp(-2 * np.pi * cutoff / SR)
    y = np.zeros_like(noise)
    for i in range(1, len(noise)):
        y[i] = y[i - 1] + alpha[i] * (noise[i] - y[i - 1])
    return y * np.hanning(len(t))


def _riser(dur: float, seed: int) -> np.ndarray:
    """Riser: noise yang filternya makin terbuka + nada naik; makin keras, berhenti di puncak."""
    t = _t(dur)
    rng = np.random.default_rng(seed)
    noise = rng.standard_normal(len(t))
    prog = t / t[-1]
    cutoff = 400 + 9000 * prog ** 2
    alpha = 1 - np.exp(-2 * np.pi * cutoff / SR)
    y = np.zeros_like(noise)
    for i in range(1, len(noise)):
        y[i] = y[i - 1] + alpha[i] * (noise[i] - y[i - 1])
    tone = _osc(180 + 900 * prog ** 1.6) * 0.35
    env = prog ** 2.2
    return (y * 0.8 + tone) * env


def riser_short() -> np.ndarray:
    return _riser(1.0, 21)


def riser_long() -> np.ndarray:
    return _riser(2.0, 22)


def _lowpass_sweep(noise: np.ndarray, cutoff: np.ndarray) -> np.ndarray:
    alpha = 1 - np.exp(-2 * np.pi * np.clip(cutoff, 20, SR / 2.2) / SR)
    y = np.zeros_like(noise)
    for i in range(1, len(noise)):
        y[i] = y[i - 1] + alpha[i] * (noise[i] - y[i - 1])
    return y


def _whoosh(dur: float, seed: int, lo: float, hi: float, peak_at: float) -> np.ndarray:
    """Noise yang filternya terbuka lalu menutup; amplitudo memuncak di `peak_at` (0–1)."""
    t = _t(dur)
    p = t / t[-1]
    shape = np.where(p < peak_at, (p / peak_at) ** 2, ((1 - p) / (1 - peak_at)) ** 1.5)
    noise = np.random.default_rng(seed).standard_normal(len(t))
    y = _lowpass_sweep(noise, lo + (hi - lo) * shape)
    y = y - _lowpass_sweep(y, np.full(len(t), 150.0))          # buang dengung rendah
    return y * shape


def whoosh_soft() -> np.ndarray:
    return _whoosh(0.55, 31, 300, 3500, 0.55)


def whoosh_fast() -> np.ndarray:
    return _whoosh(0.30, 32, 600, 8000, 0.45)


def _hit(dur: float, f_hi: float, f_lo: float, decay: float, click: float, seed: int) -> np.ndarray:
    t = _t(dur)
    body = np.tanh(2.0 * _osc(f_lo + (f_hi - f_lo) * np.exp(-30 * t))) * np.exp(-decay * t)
    n = np.random.default_rng(seed).standard_normal(len(t)) * np.exp(-120 * t) * click
    return body + n


def hit_punch() -> np.ndarray:
    return _hit(0.35, 160, 55, 14, 0.6, 41)


def hit_tight() -> np.ndarray:
    return _hit(0.22, 220, 80, 24, 0.9, 42)


def _dark(x: np.ndarray, hz: float, passes: int = 4) -> np.ndarray:
    """Low-pass bertingkat (curam) supaya benar-benar gelap."""
    for _ in range(passes):
        x = _lowpass_sweep(x, np.full(len(x), hz))
    return x


def boom() -> np.ndarray:
    t = _t(2.2)
    sub = np.sin(2 * np.pi * np.cumsum(30 + 70 * np.exp(-6 * t)) / SR) * np.exp(-1.6 * t)
    rumble = _dark(np.random.default_rng(43).standard_normal(len(t)), 140.0)
    rumble = rumble / (np.abs(rumble).max() + 1e-9)
    thump = _dark(_hit(2.2, 160, 50, 18, 0.0, 44), 400.0, 2)
    return _dark(sub + 0.6 * rumble * np.exp(-2.5 * t) + 0.5 * thump, 300.0, 2)


def record_scratch() -> np.ndarray:
    """Dua gesekan cepat: noise berpita dengan nada maju-mundur."""
    t = _t(0.42)
    rate = np.where(t < 0.18, 1 - t / 0.18, (t - 0.18) / 0.24)          # piringan dihentikan lalu ditarik
    carrier = _osc(300 + 1700 * rate)
    noise = _lowpass_sweep(np.random.default_rng(45).standard_normal(len(t)), 800 + 5000 * rate)
    env = np.minimum(1, t / 0.01) * (0.35 + 0.65 * rate)
    return (0.5 * carrier + noise * 2.0) * env


def ding_gameshow() -> np.ndarray:
    """'Benar!' — dua nada naik (C6 → G6) dengan harmonik lonceng."""
    t = _t(0.9)
    x = np.zeros_like(t)
    for d, f in ((0.0, 1046.5), (0.13, 1568.0)):
        tt = np.clip(t - d, 0, None)
        env = np.where(t >= d, np.exp(-5 * tt), 0)
        x += env * (np.sin(2 * np.pi * f * tt) + 0.4 * np.sin(2 * np.pi * 2 * f * tt) + 0.2 * np.sin(2 * np.pi * 3 * f * tt))
    return x


def buzzer() -> np.ndarray:
    """'Salah!' — dengung kotak rendah, dua ketukan."""
    t = _t(0.7)
    sq = np.sign(np.sin(2 * np.pi * 110 * t)) + 0.6 * np.sign(np.sin(2 * np.pi * 116 * t))
    gate = ((t % 0.35) < 0.28).astype(float)
    soft = _dark(sq, 1800.0, 3)                      # tumpulkan harmonik tinggi yang menusuk
    return soft * gate * np.exp(-0.8 * t) * 0.45     # sengaja lebih pelan: bunyinya padat


SOUNDS = {
    "sfx_boing": boing,
    "sfx_bass_drop": bass_drop,
    "sfx_chime": chime,
    "sfx_tick": tick,
    "sfx_swipe": swipe,
    "sfx_riser_short": riser_short,
    "sfx_riser_long": riser_long,
    "sfx_whoosh_soft": whoosh_soft,
    "sfx_whoosh_fast": whoosh_fast,
    "sfx_hit_punch": hit_punch,
    "sfx_hit_tight": hit_tight,
    "sfx_boom": boom,
    "sfx_record_scratch": record_scratch,
    "sfx_ding_gameshow": ding_gameshow,
    "sfx_buzzer": buzzer,
}


if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    for name, fn in SOUNDS.items():
        path = os.path.join(OUT, f"{name}.wav")
        out = _norm(fn(), peak_db=-9.0 if name == "sfx_buzzer" else -3.0)
        sf.write(path, _fade(out, ms_out=4 if name.startswith("sfx_riser") else 20), SR)
        print("tulis", path)
