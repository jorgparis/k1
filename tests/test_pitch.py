"""The post-processing pitch shifter.

The wolf's post-chalk line has to clear OneCore's ~148 Hz pitch ceiling, so the
lift happens after synthesis: resample (raises pitch, shortens) then WSOLA
(widens back, keeps pitch). These checks pin the two properties the story
depends on - the pitch ratio is what was asked for, and the clip keeps its
length - plus the things that would break a run: NaN samples, clipping, and a
time-stretch that smears the envelope into unintelligibility.
"""
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import ROOT, Report  # noqa: E402

sys.path.insert(0, str(ROOT / "tools"))
import pitch_shifter  # noqa: E402

RATE = 44100
report = Report("pitch shifter")


def tone(freq: float, seconds: float, harmonics: int = 10) -> list[float]:
    n = int(seconds * RATE)
    return [sum((1.0 / k) * math.sin(2 * math.pi * k * freq * i / RATE)
                for k in range(1, harmonics + 1)) / 2.5 for i in range(n)]


def goertzel(x: list[float], freq: float) -> float:
    w = 2 * math.pi * freq / RATE
    c = 2 * math.cos(w)
    s1 = s2 = 0.0
    for v in x:
        s0 = v + c * s1 - s2
        s2, s1 = s1, s0
    return math.sqrt(s1 * s1 + s2 * s2 - c * s1 * s2) / len(x)


def envelope(x: list[float], win_s: float = 0.02) -> list[float]:
    win = int(win_s * RATE)
    return [math.sqrt(sum(v * v for v in x[i:i + win]) / win)
            for i in range(0, len(x) - win, win)]


def corr(a: list[float], b: list[float]) -> float:
    n = min(len(a), len(b))
    a, b = a[:n], b[:n]
    ma, mb = sum(a) / n, sum(b) / n
    num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    da = math.sqrt(sum((x - ma) ** 2 for x in a))
    db = math.sqrt(sum((y - mb) ** 2 for y in b))
    return num / (da * db + 1e-12)


report.section("identity and edges")
src = tone(150.0, 1.5)
same = pitch_shifter.pitch_shift(src, 1.0, RATE)
report.check("ratio 1.0 is a true no-op", same == src)
report.check("empty input survives", pitch_shifter.pitch_shift([], 1.75, RATE) == [])
report.check("a zero ratio does not explode", pitch_shifter.pitch_shift(src, 0.0, RATE) == [])

report.section("pitch and length")
for ratio in (1.25, 1.50, 1.75, 2.20):
    want = 150.0 * ratio
    out = pitch_shifter.pitch_shift(src, ratio, RATE)
    got = goertzel(out, want)
    off = max(goertzel(out, want * 1.12), goertzel(out, want * 0.88))
    seconds = len(out) / RATE
    report.check(f"x{ratio:.2f} lands at {want:.1f} Hz",
                 got / off > 20, f"target/off {got / off:.0f}x")
    report.check(f"x{ratio:.2f} keeps the 1.50 s length",
                 abs(seconds - 1.5) < 0.01, f"{seconds:.3f}s")

def syllables(freq: float, seconds: float) -> list[float]:
    """A tone chopped into bursts, so its envelope has something to compare."""
    base = tone(freq, seconds)
    n = len(base)
    out = []
    for i, v in enumerate(base):
        t = i / RATE
        burst = (t % 0.34) < 0.22
        out.append(v * (1.0 if burst else 0.02))
    return out


report.section("no artefacts that break a run")
out = pitch_shifter.pitch_shift(src, 1.75, RATE)
report.check("no NaN or inf", all(math.isfinite(v) for v in out))
report.check("does not clip", max(abs(v) for v in out) <= 1.0,
             f"peak {max(abs(v) for v in out):.2f}")
speechy = syllables(150.0, 1.5)
shifted = pitch_shifter.pitch_shift(speechy, 1.75, RATE)
report.check("the syllable envelope in time is preserved",
             corr(envelope(speechy), envelope(shifted)) > 0.85,
             f"envelope r {corr(envelope(speechy), envelope(shifted)):.2f}")

report.section("the story's cast uses it once, and only where intended")
import story  # noqa: E402

shifts = {name: cast.get("shift") for name, cast in story.SEATED_CAST.items()
          if cast.get("shift")}
report.check("only the post-chalk wolf is shifted", list(shifts) == ["wolf_soft"],
             str(shifts))
report.check("the shift is the ratio chosen by ear", shifts.get("wolf_soft") == 1.75,
             str(shifts.get("wolf_soft")))
report.check("the shifted wolf keeps Paul and the dark filter",
             story.SEATED_CAST["wolf_soft"]["voice"] == "Microsoft Paul"
             and story.SEATED_CAST["wolf_soft"].get("fx")
             == story.SEATED_CAST["wolf"].get("fx"))

sys.exit(report.finish())
