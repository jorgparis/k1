"""Pitch shifting without an engine ceiling.

The Windows OneCore voices clamp SSML `pitch` near 146.8 Hz for Paul, which is
what the wolf's post-chalk line needed to beat. So the shift happens here,
after synthesis: resample the clip to change its pitch (which also shortens it
by the same ratio), then time-stretch it back with WSOLA, which keeps the
length but not the pitch change. The result is a higher voice at the original
speed and duration.

Pure stdlib on purpose, like the rest of the build: the Windows box that does
the synthesis has no numpy/scipy/sox. Cost is about 0.2 s per second of audio.
"""

import math

DEFAULT_WINDOW_S = 0.032


def resample(samples: list[float], step: float) -> list[float]:
    """y[i] = x[i * step]; step > 1 shortens the clip and raises its pitch."""
    if not samples or step <= 0:
        return []
    out_len = int(round(len(samples) / step))
    out = [0.0] * out_len
    last = len(samples) - 1
    for i in range(out_len):
        pos = i * step
        left = int(pos)
        frac = pos - left
        if left >= last:
            out[i] = samples[last]
        else:
            a = samples[left]
            out[i] = a + (samples[left + 1] - a) * frac
    return out


def stretch(samples: list[float], factor: float, rate: int) -> list[float]:
    """WSOLA time-stretch: factor > 1 lengthens the clip, pitch unchanged.

    Overlapping frames are taken from positions chosen to keep the waveform
    continuous (normalised cross-correlation against the previous frame), which
    is what stops the naive overlap-add from sounding granular.
    """
    if factor <= 0 or not samples or abs(factor - 1.0) < 1e-9:
        return list(samples)

    frame = max(64, int(round(DEFAULT_WINDOW_S * rate / 2)) * 2)
    overlap = frame // 2
    hop_out = overlap
    hop_in = hop_out / factor
    search = max(32, min(int(hop_in) // 2, int(round(0.008 * rate))))

    window = [0.5 - 0.5 * math.cos(2 * math.pi * i / frame) for i in range(frame)]
    pad = frame
    x = [0.0] * pad + list(samples) + [0.0] * frame
    total = int(round(len(x) * factor))
    out = [0.0] * (total + frame)
    acc = [0.0] * (total + frame)

    prev = None
    k = 0
    while True:
        w = k * hop_out
        if w >= total:
            break
        ideal = int(round(k * hop_in))
        if ideal >= len(x) - frame:
            break
        start = ideal
        if prev is not None:
            ref = x[prev + hop_out: prev + hop_out + overlap]
            if len(ref) == overlap:
                lo = max(0, ideal - search)
                hi = min(len(x) - frame, ideal + search)
                start = _best_offset(x, ref, lo, hi, overlap)
        for j in range(frame):
            v = x[start + j] * window[j]
            out[w + j] += v
            acc[w + j] += window[j]
        prev = start
        k += 1

    skip = int(round(pad * factor))
    want = int(round(len(samples) * factor))
    result = [0.0] * want
    for i in range(want):
        a = acc[skip + i]
        result[i] = out[skip + i] / a if a > 1e-6 else 0.0
    return result


def _best_offset(x: list[float], ref: list[float], lo: int, hi: int,
                 overlap: int) -> int:
    """Offset in [lo, hi] whose overlap best matches `ref` (coarse then fine)."""

    def score(d: int, stride: int) -> float:
        n = e = c = 0.0
        for j in range(0, overlap, stride):
            a = x[d + j]
            b = ref[j]
            n += a * b
            e += a * a
            c += b * b
        return n / math.sqrt(e * c + 1e-12)

    best = lo
    best_score = -2.0
    for d in range(lo, hi + 1, 4):
        s = score(d, 4)
        if s > best_score:
            best_score, best = s, d
    for d in range(max(lo, best - 4), min(hi, best + 4) + 1):
        s = score(d, 1)
        if s > best_score:
            best_score, best = s, d
    return best


def pitch_shift(samples: list[float], ratio: float, rate: int) -> list[float]:
    """Raise pitch by `ratio` (1.25 = 4 semitones) at unchanged length."""
    if abs(ratio - 1.0) < 1e-9:
        return list(samples)
    return stretch(resample(samples, ratio), ratio, rate)
