"""Decode a K1 stunt script (`.sh`) into keyframes the WebGL model can play.

The format, as far as the 131 scripts on the card prove it:

    one record = 32 bytes

      bytes  0.. 3   joints this robot does not have (waist/torso on bigger
                     Robosen models); ignored for the K1
      bytes  4..20   the 17 servos, in `robot.json` index order
      byte  21..27   unused (zero)
      byte  28       travel time for the move onto this keyframe (1..100; the
                     app defaults are 30..40). A *larger* value is a *slower*
                     move; see `TRAVEL_MS` below.
      byte  29       dwell at this keyframe: each unit adds about 100 ms to the
                     move that arrives at it (`FLAG_MS`). A run of records with
                     a non-zero value is also the block a routine repeats -
                     `Swing Arms` 1..4 is A,B,A,B, `Twist Butt` 1..6 is
                     A,B,A,B,A,B, `Push Ups` 4..6 is A,B,A - but the *count* is
                     nowhere in the file, so `--repeat` supplies it
      bytes 30..31   record tag: `0b xx` first record (xx = action id),
                     `fc fc` a normal keyframe, `fb fb` the last one

The 17 servo slots are byte = magnitude of travel away from that servo's
neutral, in raw counts. Two independent facts pin the scale:

  * `SpeAction/lift your arms.sh` carries 190 on both shoulder slots, and the
    shoulder's long travel is exactly 212-22 = 190;
  * the largest calf value in the corpus is 160, and the calf's long travel is
    exactly 220-60 = 160.

So a slot is not a servo position, it is "how far to move, in counts". Which
way is the model's business (see `K1.ACTION_DIR` in the demo's
`~/echange/k1/src/k1.js`); the file only says how much.

Keyframe arrival times are not stored, so this tool derives them - and the
derivation is measured, not assumed. The robot reports `progress` after every
record, so a recorded bench trace is a direct read of when each keyframe is
reached. Across 150 clean record boundaries the segment length is *not* a
function of how far the joints travel (that correlation is ~0): it is a
function of byte 28 alone, one whole 64 Hz tick table, and a *larger* byte is a
*longer* move - `Left Punch` strikes on 25 and returns on 15. The byte is
therefore a smoothness/slowness setting, whatever the app calls it; the table is
`TRAVEL_MS` below. Byte 29, the "emphasis", does have a unit after all: each
unit adds about 100 ms to the move that arrives at that keyframe (`Propose`
record 2 is exactly its 1.0 s base plus 85 units). The first record is a
lead-in of the same 1.0 s plus ~0.1 s.

That model reproduces the robot's own totals to 98-99 % on every one-shot
action. It still cannot know a repeat count (`--repeat`), so a repeated block
rescaled to its measured total can still be off; the demo prefers the bench
trace itself where one exists. The old model - `max joint change / speed`,
rescaled to the declared clock - is what made the demo's Back Flip redistribute
its phases: its file clock was right, but the arms' 1 s heave and a 0.3 s step
were swapped.

A byte is always a position, including 0: the servo goes back to neutral.
That looks wrong in Push Ups, whose pump block stores 0 on both thighs while
the arms are pumping, as if the robot stood up in the middle of its own
push-up. It is not wrong - a robot lying face down has its legs straight, and
straight legs are the thighs' neutral. The block reads as a push-up only once
the *body* is on the floor, which is a display problem, not a missing byte.

The one thing a file cannot say is which parts are touching the ground.
`--contact hands` records that: the action rests on its palms and toes at
once, so the demo solves the body pitch for two floor contacts instead of
standing it on flat soles. `--contact seat` records a floor sit: the body is
supported by the pelvis and legs, so the display keeps the torso upright and
lowers the whole model until its lowest part touches the floor. The decoded
data keeps `mag` exactly as the bytes are; the flag is a rendering hint, not a
rewrite.

The other thing a file cannot say is how many times a routine plays its marked
block. `Push Ups` stores one pump (elbow 40 -> 100 -> 40) and the robot pumps
three times; `Do Squats` stores one squat and the robot squats for 18 s;
`Swing Arms` writes its swing out twice and keeps swinging. Repetition is a
playback decision, not a keyframe. `--repeat N` plays the marked block N times
and records what it did, so the count is at least visible instead of silent:

    python tools/shdecode.py --list ~/K1Copy
    python tools/shdecode.py "~/K1Copy/ProAction/Push Ups.sh" --duration-ms 9600
    python tools/shdecode.py "~/K1Copy/ProAction/Push Ups.sh" \
        --contact hands --repeat 3 --js src/actions.js

Nothing here writes to the card.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from k1lib import spec  # noqa: E402

RECORD = 32
JOIN_AT = 4          # first servo slot
JOIN_COUNT = 17
SPEED_AT = 28
TAG_AT = 30

# robot.json order, i.e. servo index 0..16. This is the order inside a record.
ORDER = [
    "leftThigh", "leftCalf", "leftAnkle",
    "rightThigh", "rightCalf", "rightAnkle",
    "leftShoulder", "rightShoulder",
    "leftHip", "leftFoot", "rightHip", "rightFoot",
    "leftArm", "leftHand", "rightArm", "rightHand",
    "head",
]

# Measured travel time of the move onto a keyframe, by byte 28. Medians of 150
# clean record boundaries in the robot's own progress traces; a larger byte is
# a slower move (`Left Punch` strikes on 25 and returns on 15).
TRAVEL_MS = {
    15: 196, 20: 312, 25: 453, 30: 609, 35: 797, 40: 1000, 45: 1250, 50: 1438,
}
# Byte 29 adds time after all: ~100 ms per unit (Propose record 2 is 1000 ms of
# base plus 85 units, 9515 ms measured).
FLAG_MS = 100
# The first record arrives one base move plus this much after the command; the
# robot's own traces put the lead-in at base + 78..141 ms.
LEAD_MS = 110


class ShFormatError(Exception):
    pass


_SPEC_BY_NORM = None


def records(path: Path) -> list[bytes]:
    data = path.read_bytes()
    if not data or len(data) % RECORD:
        raise ShFormatError(f"{path.name}: {len(data)} bytes is not a multiple of {RECORD}")
    return [data[i:i + RECORD] for i in range(0, len(data), RECORD)]


def tag(rec: bytes) -> str:
    return rec[TAG_AT:TAG_AT + 2].hex()


def frames(path: Path) -> dict:
    """Read one script into named magnitudes, speeds and record tags."""
    recs = records(path)
    out = []
    for i, r in enumerate(recs):
        mags = {name: r[JOIN_AT + k] for k, name in enumerate(ORDER)}
        out.append({
            "i": i,
            "mag": mags,
            "speed": r[SPEED_AT],
            "tag": tag(r),
            "extra": list(r[:JOIN_AT]),           # torso joints the K1 lacks
            "flags": r[29],
        })
    return {"name": path.stem, "folder": path.parent.name, "records": out}


def travel_ms(speed: int) -> float:
    """How long the move onto a keyframe carrying `speed` takes, in ms.

    Measured, not invented: the medians of 150 clean record boundaries in the
    robot's own progress traces (speeds 15..50). Every measured value sits on
    a whole 64 Hz tick (15.625 ms), which is why individual samples land a tick
    either side of the median.

    The table is a smooth curve - T ~ 2.11 * speed^(5/3) fits it within a
    couple of percent - so speeds between the measured points interpolate
    geometrically and speeds outside them follow that curve. The result is
    only ever used as a weight and then rescaled to the action's measured
    clock, so the shape is what matters, not the absolute millisecond.
    """
    s = max(1, int(speed))
    keys = sorted(TRAVEL_MS)
    if s in TRAVEL_MS:
        return float(TRAVEL_MS[s])
    if keys[0] < s < keys[-1]:
        lo = max(k for k in keys if k < s)
        hi = min(k for k in keys if k > s)
        u = (math.log(s) - math.log(lo)) / (math.log(hi) - math.log(lo))
        a, b = math.log(TRAVEL_MS[lo]), math.log(TRAVEL_MS[hi])
        return math.exp(a + u * (b - a))
    return 2.11 * s ** (5.0 / 3.0)


def durations(fr: dict, total_ms: int) -> list[int]:
    """When each keyframe is *reached*, in ms.

    Stored timing is not in the file, so this is derived - but from the robot's
    own measured travel table, not from joint distance. See `travel_ms` and the
    module docstring: the move onto a keyframe is timed by byte 28 (larger =
    slower), byte 29 adds `FLAG_MS` per unit, and the first record is a lead-in
    of the same base plus `LEAD_MS`. The weights are rescaled to `total_ms` so
    the action still lands exactly on its measured/declared clock; that keeps
    the shape honest without pretending the model beats the measurement.
    """
    recs = fr["records"]
    weights = []
    for k, r in enumerate(recs):
        w = travel_ms(r["speed"]) + FLAG_MS * r["flags"]
        if k == 0:
            w += LEAD_MS
        weights.append(w)
    if not any(weights):
        weights = [1.0 for _ in recs]
    unit = total_ms / sum(weights)
    arrived, t = [], 0.0
    for w in weights:
        t += w * unit
        arrived.append(int(round(t)))
    arrived[-1] = total_ms                     # land exactly on the measured clock
    return arrived


def norm(name: str) -> str:
    """Card filenames differ from robot.json in case and punctuation
    (`Bow, please` vs `Bow, Please`), so match on letters and digits only."""
    return "".join(ch for ch in name.lower() if ch.isalnum())


def declare_ms(name: str) -> int | None:
    global _SPEC_BY_NORM
    if _SPEC_BY_NORM is None:
        _SPEC_BY_NORM = {norm(k): v for k, v in spec.actions().items()}
    entry = _SPEC_BY_NORM.get(norm(name))
    return entry["duration_ms"] if entry else None


def marked_block(fr: dict) -> tuple[int, int] | None:
    """The longest run of records with byte 29 set, as inclusive indices.

    That run is the part a routine repeats (`Swing Arms` 1..4, `Twist Butt`
    1..6, `Push Ups` 4..6). The file marks the block but never says how often,
    so this is only the *where*.
    """
    best = None
    i, recs = 0, fr["records"]
    while i < len(recs):
        if recs[i]["flags"]:
            j = i
            while j + 1 < len(recs) and recs[j + 1]["flags"]:
                j += 1
            if best is None or j - i > best[1] - best[0]:
                best = (i, j)
            i = j + 1
        else:
            i += 1
    return best


def expand(fr: dict, times: int) -> dict:
    """Play the marked block `times` times, keeping every other record once."""
    block = marked_block(fr)
    if block is None:
        raise ShFormatError(f'{fr["name"]}: no record carries byte 29, '
                            'so there is no repeating block to find')
    if block[1] == block[0]:
        raise ShFormatError(f'{fr["name"]}: only record {block[0]} is marked, '
                            'which is a hold, not a block of two or more poses')
    s, e = block
    core = fr["records"][s:e + 1]
    out = dict(fr)
    out["records"] = fr["records"][:s] + core * times + fr["records"][e + 1:]
    out["repeat"] = {"core": list(range(s, e + 1)), "times": times}
    return out


def decode(path: Path, total_ms: int | None = None, contact: str | None = None,
           repeat: int = 1) -> dict:
    fr = frames(path)
    if repeat > 1:
        fr = expand(fr, repeat)
    if total_ms is None:
        total_ms = declare_ms(fr["name"]) or 2000
    ms = durations(fr, total_ms)

    poses = []
    for r, at in zip(fr["records"], ms):
        poses.append({
            "t": round(at / 1000, 3),          # when this keyframe is reached
            "speed": r["speed"],
            "tag": r["tag"],
            "mag": r["mag"],
        })

    dec = {
        "name": fr["name"],
        "folder": fr["folder"],
        "source": f'{fr["folder"]}/{fr["name"]}.sh',
        "duration_ms": total_ms,
        "declared": declare_ms(fr["name"]) is not None,
        "order": ORDER,
        "frames": poses,
    }
    if contact:
        dec["contact"] = contact
    if fr.get("repeat"):
        dec["repeat"] = fr["repeat"]
    return dec


def clamp_report(dec: dict) -> list[str]:
    """Which magnitudes exceed a servo's own travel (the file does overshoot)."""
    out = []
    for pose in dec["frames"]:
        for name in ORDER:
            j = spec.joints()[name]
            travel = max(j["max"] - j["neutral"], j["neutral"] - j["min"])
            if pose["mag"][name] > travel:
                out.append(f'{pose["t"]:6.2f}s {name} mag {pose["mag"][name]} > travel {travel}')
    return out


def show(dec: dict) -> None:
    print(f'{dec["source"]}  '
          f'{len(dec["frames"])} keyframes, {dec["duration_ms"]} ms '
          f'({"declared" if dec["declared"] else "assumed"})')
    cols = [n for n in ORDER if any(p["mag"][n] for p in dec["frames"])] or ["(static)"]
    print("   at   speed  " + " ".join(f"{c[:5]:>5s}" for c in cols))
    for p in dec["frames"]:
        vals = " ".join(f'{p["mag"][c]:5d}' for c in cols)
        print(f'{p["t"]:6.2f} {p["speed"]:4d}  {vals}')
    over = clamp_report(dec)
    if over:
        print(f"\n  {len(over)} magnitude(s) exceed the servo's travel; the demo clamps:")
        for line in over[:8]:
            print("   ", line)
    if dec.get("contact"):
        where = ("the body settles onto the floor"
                 if dec["contact"] == "seat"
                 else "the demo plants those on the floor")
        print(f'\n  contact: {dec["contact"]} - {where}')
    if dec.get("repeat"):
        r = dec["repeat"]
        print(f'\n  repeat: records {r["core"][0]}..{r["core"][-1]} played '
              f'{r["times"]}x (byte 29 marks the block, not the count)')


def listing(root: Path) -> None:
    files = sorted(root.glob("*/*.sh"))
    if not files:
        print(f"no *.sh under {root}", file=sys.stderr)
        return
    print(f"{len(files)} scripts under {root}\n")
    for folder in ("Action", "ProAction", "SpeAction"):
        fs = [f for f in files if f.parent.name == folder]
        if not fs:
            continue
        print(f"--- {folder} ({len(fs)})")
        for f in fs:
            recs = records(f)
            ms = declare_ms(f.stem)
            clock = f"{ms / 1000:5.1f}s" if ms else "     ?"
            print(f"  {clock}  {len(recs):3d} rec  {tag(recs[0])}  {f.stem}")
        print()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("path", nargs="?", help="a .sh file, or a card root with --list")
    ap.add_argument("--list", action="store_true", help="list every script under a card root")
    ap.add_argument("--duration-ms", type=int, help="override the declared clock")
    ap.add_argument("--json", help="write the decoded keyframes here")
    ap.add_argument("--js", help="write a window.K1ACTIONS module here")
    ap.add_argument("--contact", choices=("hands", "seat"),
                    help="what carries the body: 'hands' rests on palms and toes, "
                         "'seat' is a floor sit with the pelvis and legs down")
    ap.add_argument("--repeat", type=int, default=1, metavar="N",
                    help="play the block byte 29 marks N times (the count is not in the "
                         "file; Push Ups is 3 on the robot)")
    args = ap.parse_args(argv)

    if not args.path:
        ap.error("give a .sh path, or a card root with --list")
    path = Path(args.path).expanduser()

    if args.list:
        listing(path)
        return 0

    try:
        dec = decode(path, args.duration_ms, args.contact, args.repeat)
    except ShFormatError as err:
        print(f"shdecode: {err}", file=sys.stderr)
        return 2
    show(dec)

    if args.json:
        Path(args.json).write_text(json.dumps(dec, indent=1) + "\n", encoding="utf-8")
        print(f"\nwrote {args.json}")
    if args.js:
        key = "".join(ch for ch in dec["name"].lower() if ch.isalnum())
        body = json.dumps({key: dec}, indent=1)
        note = ""
        if dec.get("contact"):
            if dec["contact"] == "seat":
                note = ("\n   `contact` is a rendering hint, not a rewrite: this take sits\n"
                        "   the body on the floor, so the demo keeps the torso upright and\n"
                        "   lowers the whole model until its lowest part touches.\n")
            else:
                note = ("\n   `contact` is a rendering hint, not a rewrite: this pose rests on\n"
                        "   its hands as well as its feet, so the demo solves the body pitch\n"
                        "   for two floor contacts instead of standing it on flat soles.\n")
        if dec.get("repeat"):
            r = dec["repeat"]
            note += ("\n   `repeat` is not in the bytes either: byte 29 marks records\n"
                     f'   {r["core"][0]}..{r["core"][-1]} as the block a routine repeats, but the count\n'
                     f'   is a playback decision. {r["times"]}x is what the robot does.\n')
        Path(args.js).write_text(
            "/* Generated by tools/shdecode.py from "
            f'{dec["source"]} - do not hand-edit.\n\n'
            "   Decoded from the robot's own SD card, not invented: magnitudes are\n"
            "   the bytes, `t` is when each keyframe is reached, and the clock is\n"
            "   robot.json's declared duration. The .sh does not store direction or\n"
            "   timing, so K1.actionPose in k1.js supplies the direction and this\n"
            "   tool derives the timing - see the shdecode docstring.\n"
            f"{note}*/\n"
            f"window.K1ACTIONS = {body};\n", encoding="utf-8")
        print(f"wrote {args.js} as K1ACTIONS.{key}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
