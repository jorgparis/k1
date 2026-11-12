#!/usr/bin/env python3
"""Generate src/actions.js from the phone's move list and the card's .sh files.

The phone page (``tools/phone.py`` in the k1 checkout) is the source of truth
for *which* moves exist: 68 actions robot.json names, plus the card-only scripts
the app exposes anyway. This script resolves each move to the same script the
phone plays, decodes it with ``tools/shdecode.py``, and bakes the measured clock
into the keyframe times so the WebGL player cannot drift from the hardware.

Timing provenance, strongest first:

  measured   the owner timed the action on the robot (all 68 spec actions and
             the card-only moves in CARD_MOVES)
  declared   robot.json's own number; no hardware clock has been taken
  estimated  a card-only script nobody has timed. Its length is only a guess:
             records x 700 ms, the median of the measured card-only scripts.
             The UI says "estimated", so it is never mistaken for a measurement.
  unavailable no .sh exists on the card (four spec actions are like this)

Every entry also keeps ``repeat`` and ``contact`` where the bytes do not say
them: Push Ups stores one pump and the robot pumps three times, and it rests on
its palms as well as its toes.

    python3 test/genactions.py
    python3 test/genactions.py --k1 /path/to/k1 --out src/actions.js
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent          # this demo's folder


def default_k1() -> Path:
    """Find the k1 checkout without baking in a machine path.

    K1_ROOT wins; then a checkout that contains this demo (``webgl/k1`` at the
    k1 repo root); then ``~/k1``. Pass ``--k1`` if none of those fit.
    """
    env = os.environ.get("K1_ROOT")
    if env:
        return Path(env)
    inside = HERE.parent.parent
    if (inside / "tools" / "phone.py").is_file():
        return inside
    return Path.home() / "k1"


DEFAULT_K1 = default_k1()
EST_MS_PER_RECORD = 700


def load_k1(root: Path):
    """Import phone.py, shdecode.py and the spec from a k1 checkout."""
    tools = root / "tools"
    sys.path.insert(0, str(root))
    sys.path.insert(0, str(tools))
    import phone          # noqa: E402
    import shdecode       # noqa: E402
    from k1lib import spec  # noqa: E402
    return phone, shdecode, spec


def script_roots(k1: Path) -> list[Path]:
    """Where a move's .sh can live, in preference order.

    The derived look/wave/sit scripts are not in the read-only card mirror:
    they were built in the k1 checkout and copied to the card from there.
    ProAction wins over SpeAction because robot.json names ProAction paths.
    """
    mirror = Path.home() / "K1Copy"
    return [
        k1 / "build" / "sit" / "final",
        k1 / "build" / "look" / "final",
        k1 / "build" / "wave" / "final",
        mirror / "ProAction",
        mirror / "SpeAction",
        mirror / "Action",
    ]


def index_scripts(k1: Path, shdecode) -> dict[str, Path]:
    by_norm: dict[str, Path] = {}
    for root in script_roots(k1):
        if not root.is_dir():
            continue
        for path in sorted(root.glob("*.sh")):
            by_norm.setdefault(shdecode.norm(path.stem), path)
    return by_norm


def measured_ms_per_record(phone, shdecode, spec, by_norm: dict[str, Path]) -> float:
    """Median ms/record of the card-only moves that *were* timed on hardware."""
    values = []
    for move in phone.move_entries(spec.actions()):
        if move["group"] != "Card" or move["source"] != "measured":
            continue
        path = by_norm.get(shdecode.norm(move["name"]))
        if not path:
            continue
        records = len(path.read_bytes()) // 32
        if records:
            values.append(move["seconds"] * 1000.0 / records)
    return statistics.median(values) if values else EST_MS_PER_RECORD


def build(k1: Path) -> tuple[dict, list[str]]:
    phone, shdecode, spec = load_k1(k1)
    by_norm = index_scripts(k1, shdecode)
    per_record = measured_ms_per_record(phone, shdecode, spec, by_norm)

    actions: dict[str, dict] = {}
    order: list[str] = []
    for move in phone.move_entries(spec.actions()):
        key = shdecode.norm(move["name"])
        if key in actions:
            raise SystemExit(f"two moves normalise to the same key: {key!r}")
        path = by_norm.get(key)
        declared = shdecode.declare_ms(move["name"])

        if path is None:
            clock, duration_ms, reason = "declared", int(round(move["seconds"] * 1000)), "no .sh on the card"
            if declared is None:
                clock, duration_ms = "unknown", None
        elif move["source"] == "measured":
            clock, duration_ms = "measured", int(round(move["seconds"] * 1000))
        elif move["source"] == "declared" and declared is not None:
            clock, duration_ms = "declared", declared
        else:
            records = len(path.read_bytes()) // 32
            clock = "estimated"
            duration_ms = int(round(records * per_record))

        entry = {
            "name": move["name"],
            "key": key,
            "folder": move["group"],
            "source": f"{move['path']}.sh",
            "path": move["path"],
            "duration_ms": duration_ms,
            "clock": clock,
            "declared": declared is not None,
            "measured": clock == "measured",
            "available": path is not None,
            "note": move["note"],
        }
        if path is None:
            entry["reason"] = reason
            entry["frames"] = []
        else:
            contact = "hands" if key == "pushups" else None
            repeat = 3 if key == "pushups" else 1
            dec = shdecode.decode(path, duration_ms, contact, repeat)
            entry["frames"] = dec["frames"]
            if "contact" in dec:
                entry["contact"] = dec["contact"]
            if "repeat" in dec:
                entry["repeat"] = dec["repeat"]
            # The decoder's own `source` may use the mirror's spelling
            # (`Bow, please`); the phone's path is the one robots.json names.
            entry["file"] = dec["source"]

        actions[key] = entry
        order.append(key)

    return actions, order


def render(actions: dict, order: list[str]) -> str:
    counts = {c: sum(1 for k in order if actions[k]["clock"] == c)
              for c in ("measured", "declared", "estimated", "unknown")}
    unavailable = sum(1 for k in order if not actions[k]["available"])
    out = [
        "/* Generated by test/genactions.py from the phone's own move list and",
        "   the robot's own SD card scripts - do not hand-edit.",
        "",
        "   One entry per move the phone page can play. `frames` are decoded from",
        "   the bytes (magnitude, speed, tag); `t` is when each keyframe is",
        "   reached and ends exactly on `duration_ms`.",
        "",
        "   `clock` is the timing provenance, not a guess dressed up as data:",
        "     measured   timed on the robot",
        "     declared   robot.json's number, never timed on hardware",
        "     estimated  card-only script, length derived from its record count",
        "     unknown    no script at all (the move is listed, disabled)",
        f"   This build: {len(order)} moves, {counts['measured']} measured,",
        f"   {counts['declared']} declared, {counts['estimated']} estimated,",
        f"   {counts['unknown']} unknown, {unavailable} not on the card.",
        "",
        "   `repeat` and `contact` are playback decisions, not bytes: Push Ups",
        "   stores one pump and the robot pumps three times, and it rests on its",
        "   palms as well as its toes. */",
        "window.K1ACTIONS = {",
    ]
    for n, key in enumerate(order):
        a = actions[key]
        frames = a.pop("frames")
        out.append(f' "{key}": {{')
        fields = [k for k in a if k not in ()]
        for i, field in enumerate(fields):
            out.append(f'  {json.dumps(field)}: {json.dumps(a[field], ensure_ascii=True)},')
        out.append('  "frames": [')
        for i, frame in enumerate(frames):
            comma = "," if i + 1 < len(frames) else ""
            out.append("   " + json.dumps(frame, separators=(",", ":")) + comma)
        out.append("  ]")
        out.append(" }" + ("," if n + 1 < len(order) else ""))
        a["frames"] = frames
    out.append("};")
    out.append("")
    out.append("/* The phone menu's order: Card, then ProAction, then Action, each")
    out.append("   alphabetical by the name the phone shows. */")
    out.append("window.K1ACTION_LIST = " + json.dumps(order, indent=0).replace("\n", " ") + ";")
    out.append("")
    return "\n".join(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--k1", type=Path, default=DEFAULT_K1,
                    help=f"the k1 checkout (default {DEFAULT_K1})")
    ap.add_argument("--out", type=Path, default=HERE / "src" / "actions.js",
                    help="where to write the generated module")
    args = ap.parse_args(argv)

    if not (args.k1 / "tools" / "phone.py").is_file():
        raise SystemExit(f"{args.k1} is not a k1 checkout (no tools/phone.py)")
    actions, order = build(args.k1)
    text = render(actions, order)
    args.out.write_text(text, encoding="utf-8")

    by_clock = {}
    for key in order:
        by_clock[actions[key]["clock"]] = by_clock.get(actions[key]["clock"], 0) + 1
    print(f"wrote {args.out}  ({len(order)} moves, {len(text) / 1024:.0f} kB)")
    print("  " + "  ".join(f"{k} {v}" for k, v in sorted(by_clock.items())))
    missing = [actions[k]["name"] for k in order if not actions[k]["available"]]
    if missing:
        print("  not on the card: " + ", ".join(missing))
    return 0


if __name__ == "__main__":
    sys.exit(main())
