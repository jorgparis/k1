"""Build the standalone phone player: one HTML file, no server, no laptop.

The K1 has no Wi-Fi and no way to store a routine, so a story has to be held by
something with a Bluetooth radio. Chrome on Android can be that thing through
Web Bluetooth, and this builder packs everything the page needs into a single
file: the frame codec, the routines, the action paths, and the captions taken
from each story's own manifest.

    python3 tools/phone.py
    python3 tools/phone.py --out /tmp/k1-story.html

Why a generator instead of a hand-written page: the routines are the same JSON
`k1ctl.py routine` plays. Embedding them at build time means the phone cannot
drift from the laptop — a test asserts the embedded steps are byte-identical to
`routines/story_*.json`, and every action name resolves to a spec path.

The page itself is deliberately dependency-free and makes no network requests.
It can be opened from a file, or served from Termux with
`python -m http.server 8000` if Chrome refuses Bluetooth on a file:// origin.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from k1lib import spec  # noqa: E402

import story as story_plan  # noqa: E402  (metadata only; importing builds nothing)

PHONE_DIR = ROOT / "tools" / "phone"
DEFAULT_OUT = ROOT / "build" / "phone" / "k1-story.html"

# `RobotCore.i_routine` sleeps this long after every intent step (never after an
# explicit wait). The page reproduces it so a phone performance is paced like
# the laptop one the owner has already watched, not 0.35s per beat faster.
GAP_S = 0.35

# Phone-sized copy. The routine descriptions are written for whoever is editing
# the repo; these are what the person holding the phone reads.
BLURBS = {
    "first_boot": "The K1 wakes up, tries a few things, and dies twice.",
    "derelict": "A lone survivor on a dying ship, and a cat it mistakes for an alien.",
    "derelict_fr": "Le même film, en français — blagues réécrites, onze cascades.",
    "grimm": "Un conte des frères Grimm à trois voix : le pêcheur, sa femme, et le poisson d'or.",
    "chat_botte": "Perrault à trois voix : un fils sans rien, un chat en bottes, et un ogre qui devient souris.",
    "bremen": "Quatre animaux trop vieux pour servir, une maison de voleurs, et un concert qui fait fuir tout le monde.",
    "seated": "Le Loup et les Sept Chevreaux, joué assis : la chèvre, le loup et les chevreaux, la tête qui suit chaque réplique, et la patte que le loup montre.",
    "corbeau": "La fable de La Fontaine à trois voix : le corbeau vaniteux, le renard flatteur, et le fromage qui tombe.",
    "cochons": "Les trois petits cochons à cinq voix : trois frères, trois maisons, et un loup qui souffle de plus en plus fort.",
}

# The moves menu: every spec action plus the scripts this card holds that
# robot.json never names. A card script has no declared clock, so each tuple
# carries the clock measured on hardware where one exists (None means nobody has
# timed it; the menu says so rather than inventing a number).
#
# (name, path, measured seconds or None, confirmed, note)
CARD_MOVES = [
    ("shake head", "SpeAction/shake head", 4.469, "yes", ""),
    ("look left", "SpeAction/look left", 1.375, "yes", "silent"),
    ("look right", "SpeAction/look right", 1.375, "yes", "silent"),
    ("wave left", "SpeAction/wave left", None, "yes", "plays the Wave Hands clip"),
    ("wave right", "SpeAction/wave right", None, "yes", "plays the Wave Hands clip"),
    ("sit", "SpeAction/sit", 7.625, "yes", "leaves it seated; the pose is transient"),
    ("sit look left", "SpeAction/sit look left", 1.375, "yes", "renews the seated pose; leaves it seated"),
    ("sit look right", "SpeAction/sit look right", 1.375, "yes", "renews the seated pose; leaves it seated"),
    # Derived from the seated frame plus `say hello`'s right-arm gesture; the
    # records hold the seat, so it is its own renewal. Watched and timed
    # 2026-11-10.
    ("sit show hand", "SpeAction/sit show hand", 3.578, "yes",
     "renews the seated pose; leaves it seated"),
    ("stand", "SpeAction/stand", 10.094, "yes", "assumes it is seated"),
    # The other unique SpeAction scripts: present on the card, invisible to
    # robot.json. The four arm raises were watched in the left/right pairs run.
    ("Raise Left Hand", "SpeAction/Raise Left Hand", None, "yes", ""),
    ("Raise Right Hand", "SpeAction/Raise Right Hand", None, "yes", ""),
    ("lift your left arm", "SpeAction/lift your left arm", None, "yes", ""),
    ("lift your right arm", "SpeAction/lift your right arm", None, "yes", ""),
    ("Resume Standing", "SpeAction/Resume Standing", None, "no", "reported 100% and did not move"),
    ("Chest Up", "SpeAction/Chest Up", None, None, "not watched yet"),
    ("Open Arms", "SpeAction/Open Arms", None, None, "not watched yet"),
    ("Open Legs", "SpeAction/Open Legs", None, None, "not watched yet"),
    ("Raise Your Hands", "SpeAction/Raise Your Hands", None, None, "not watched yet"),
    ("lift your arms", "SpeAction/lift your arms", None, None, "not watched yet"),
    ("pose to take off", "SpeAction/pose to take off", None, None, "not watched yet"),
    ("showtime", "SpeAction/showtime", None, None, "not watched yet"),
]
MOVE_GROUPS = ["Card", "ProAction", "Action"]
UNKNOWN_MOVE_GRACE_S = 30.0


def move_entries(actions: dict[str, dict]) -> list[dict]:
    """Every action the phone can play, with a clock and a grace for each.

    The 68 spec actions resolve through `robot.json` and prefer the hardware
    clock from `build/bench/all68.json` when this checkout has it. The card-only
    scripts cannot be looked up at all, so their clocks live in `CARD_MOVES`.
    """
    measured = story_plan.measured_actions()
    confirmed: dict[str, str] = {}
    try:
        payload = json.loads((ROOT / "build" / "bench" / "all68.json")
                             .read_text(encoding="utf-8"))
        for row in payload.get("rows", []):
            if row.get("name"):
                confirmed[row["name"]] = row.get("confirmed")
    except (OSError, ValueError):
        pass

    moves = []
    for name, entry in actions.items():
        seconds = measured.get(name)
        if seconds is None:
            seconds = measured.get(entry["path"].lower())
        source = "measured" if seconds is not None else "declared"
        seconds = (seconds / 1000.0 if seconds is not None
                   else entry["duration_ms"] / 1000.0)
        moves.append({
            "name": name,
            "path": entry["path"],
            "group": entry["folder"],
            "seconds": round(seconds, 3),
            "grace": round(seconds + story_plan.GRACE_S, 1),
            "source": source,
            "verified": confirmed.get(name),
            "note": "",
        })

    for name, path, seconds, verified, note in CARD_MOVES:
        moves.append({
            "name": name,
            "path": path,
            "group": "Card",
            "seconds": None if seconds is None else round(seconds, 3),
            "grace": (round(seconds + story_plan.GRACE_S, 1)
                      if seconds is not None else UNKNOWN_MOVE_GRACE_S),
            "source": "measured" if seconds is not None else "unknown",
            "verified": verified,
            "note": note,
        })

    moves.sort(key=lambda m: (MOVE_GROUPS.index(m["group"]), m["name"].lower()))
    return moves


def captions(steps: list[dict], timeline: list[dict], where: str) -> list[str | None]:
    """Pair each routine step with the manifest's own words for it.

    The manifest has one beat per clip/stunt; the routine has an extra `wait`
    after every one. Walk both in order and match on kind rather than index, so
    a future generator change that merges a pause cannot silently shift every
    caption by one beat.
    """
    notes: list[str | None] = []
    beat = 0
    for i, step in enumerate(steps):
        if "wait" in step:
            notes.append(None)
            continue
        if "speak" in step:
            want = ("speech", "sfx")
            is_action = False
        elif "action" in step or "actionPath" in step:
            # `renew` is the generator's label for a seated look that doubles as
            # a pose renewal. It is an actionPath step in the routine, so the
            # manifest's `renew` beats have to match here too.
            want = ("action", "renew")
            is_action = True
        else:
            raise SystemExit(f"{where}: step {i} is not speak/action/wait: {step!r}")

        while beat < len(timeline) and timeline[beat]["kind"] not in want:
            beat += 1
        if beat >= len(timeline):
            raise SystemExit(
                f"{where}: ran out of manifest beats before step {i} ({step!r}); "
                f"the routine and manifest disagree"
            )
        found = timeline[beat]
        if is_action:
            # `actionPath` is the same kind of beat, but the manifest records
            # the path (that is the only name a card-only script has).
            wanted = step.get("action", step.get("actionPath"))
            if found["detail"] != wanted:
                raise SystemExit(
                    f"{where}: step {i} is {wanted!r} but the manifest says "
                    f"{found['detail']!r}"
                )
        if not is_action and found["kind"] == "sfx":
            notes.append("♪ " + found["detail"])
        else:
            speaker = found.get("speaker")
            notes.append(f"{speaker} — {found['detail']}" if speaker
                         else found["detail"])
        beat += 1

    if beat != len(timeline):
        raise SystemExit(
            f"{where}: {len(timeline) - beat} manifest beat(s) left over; "
            f"the routine does not describe the whole timeline"
        )
    return notes


def clip_range(steps: list[dict], where: str) -> str:
    ids = []
    for i, step in enumerate(steps):
        if "speak" not in step:
            continue
        path = str(step["speak"])
        folder, _, stem = path.partition("/")
        if folder != "02" or not stem.isdigit():
            raise SystemExit(
                f"{where}: step {i} speaks {path!r}; the card's 02/ folder only "
                f"resolves numeric ids like 02/301"
            )
        ids.append(int(stem))
    ids.sort()
    if not ids:
        return "—"
    if ids == list(range(ids[0], ids[-1] + 1)):
        return f"{ids[0]}–{ids[-1]}"
    return ", ".join(str(i) for i in ids)


def expected_seconds(steps: list[dict]) -> float:
    """How long the performance should take, including i_routine's breath.

    A stunt's real clock is `grace - GRACE_S`: the generator writes measured +
    15s into every action step, so the subtraction recovers what the owner
    actually watched (Play Dead 16.8s, Open Fire 13.9s). This is used for the
    progress bar and the rehearsal, never as a completion deadline — the runner
    waits for progress-100.
    """
    total = 0.0
    for step in steps:
        if "wait" in step:
            total += float(step["wait"])
        elif "action" in step or "actionPath" in step:
            total += max(0.5, float(step["grace"]) - story_plan.GRACE_S) + GAP_S
        elif "speak" in step:
            total += GAP_S
    return total


def story_data(key: str, plan: dict, actions: dict[str, str]) -> dict:
    where = f"story {key}"
    routine_path = Path(plan["routine"])
    build = Path(plan["build"])
    if not routine_path.exists():
        raise SystemExit(f"{where}: routine missing at {routine_path}")
    manifest_path = build / "manifest.json"
    if not manifest_path.exists():
        raise SystemExit(f"{where}: manifest missing at {manifest_path}")

    routine = json.loads(routine_path.read_text(encoding="utf-8"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    steps = routine["steps"]

    for i, step in enumerate(steps):
        if "action" not in step and "actionPath" not in step:
            continue
        if "action" in step:
            name = step["action"]
            if name not in actions:
                raise SystemExit(f"{where}: step {i} names unknown action {name!r}")
        else:
            path = step["actionPath"]
            if not re.fullmatch(r"[A-Za-z]+/[^/]+", str(path)):
                raise SystemExit(
                    f"{where}: step {i} has a bad card path {path!r}; expected "
                    f"Folder/name, like SpeAction/shake head"
                )
        if not step.get("grace"):
            raise SystemExit(
                f"{where}: step {i} ({step.get('action', step.get('actionPath'))}) "
                f"has no grace; the default deadline is the declared clock + 15s, "
                f"which is short for the long stunts"
            )

    notes = captions(steps, manifest["timeline"], where)
    total = expected_seconds(steps)
    stunts = sum(1 for s in steps if "action" in s or "actionPath" in s)
    clips = sum(1 for s in steps if "speak" in s)

    return {
        "id": key,
        "title": plan["title"],
        "language": plan.get("language", "EN"),
        "blurb": BLURBS.get(key, plan["description"]),
        "steps": steps,
        "notes": notes,
        "expected": round(total, 2),
        "preview_seconds": round(float(manifest["total_seconds"]), 2),
        "stunt_count": stunts,
        "clip_count": clips,
        "clip_range": clip_range(steps, where),
        "voice": manifest.get("voice", ""),
    }


def json_for_script(value) -> str:
    """`window.K1_DATA = {...};` with the characters HTML cares about escaped.

    `</script>` cannot appear in a string literal, and `&` would otherwise be
    an HTML entity. JSON escapes are valid JavaScript, so this keeps the blob
    machine-readable while making the page safe to inline.
    """
    text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    text = (text.replace("<", "\\u003c")
                .replace(">", "\\u003e")
                .replace("&", "\\u0026")
                .replace("\u2028", "\\u2028")
                .replace("\u2029", "\\u2029"))
    return "window.K1_DATA = " + text + ";"


def script_body(text: str) -> str:
    return text.replace("</script", "<\\/script")


def build(out: Path) -> dict:
    actions = {name: entry["path"] for name, entry in spec.actions().items()}
    stories = [story_data(key, plan, actions)
               for key, plan in story_plan.STORIES.items()]

    data = {
        "gap": GAP_S,
        "settle": 6.0,
        "service": spec.service_uuid(),
        "characteristic": spec.characteristic_uuid(),
        "actions": actions,
        "moves": move_entries(spec.actions()),
        "stories": stories,
    }

    shell = (PHONE_DIR / "shell.html").read_text(encoding="utf-8")
    frames = script_body((PHONE_DIR / "frames.js").read_text(encoding="utf-8"))
    player = script_body((PHONE_DIR / "player.js").read_text(encoding="utf-8"))
    for token, body in (("/*__FRAMES__*/", frames),
                        ("/*__DATA__*/", json_for_script(data)),
                        ("/*__PLAYER__*/", player)):
        if token not in shell:
            raise SystemExit(f"shell.html is missing the {token} slot")
        shell = shell.replace(token, body, 1)

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(shell, encoding="utf-8")
    return {"path": out, "stories": stories, "bytes": len(shell.encode("utf-8")),
            "actions": actions, "moves": data["moves"]}


def windows_path(path: Path) -> str | None:
    text = str(path)
    if text.startswith("/mnt/c/"):
        return "C:\\" + text[len("/mnt/c/"):].replace("/", "\\")
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT,
                        help=f"where to write the page (default: {DEFAULT_OUT.relative_to(ROOT)})")
    args = parser.parse_args(argv)

    result = build(args.out)
    out = result["path"]
    print(f"phone player -> {out.relative_to(ROOT)}  ({result['bytes'] / 1024:.0f} KB)")
    win = windows_path(out)
    if win:
        print(f"Windows path -> {win}")
    print()
    for s in result["stories"]:
        print(f"  {s['title']:<20} {int(s['expected']) // 60}:{int(s['expected']) % 60:02d}"
              f"  {s['stunt_count']:>2} stunts  {s['clip_count']:>2} clips"
              f"  ({s['clip_range']})  {s['language']}")
    print()
    print(f"  service         {spec.service_uuid()}")
    print(f"  characteristic  {spec.characteristic_uuid()}")
    print(f"  action paths    {len(result['actions'])}")
    print(f"  moves in menu   {len(result['moves'])}")
    print()
    print("Copy the file to the phone, open it in Chrome, close the K One app,")
    print("tap Connect, pick K1-…, then Play. If Chrome refuses Bluetooth on a")
    print("file:// URL, serve it from the phone itself:")
    print("  Termux:  cd ~/k1-story && python -m http.server 8000")
    print("  Chrome:  http://localhost:8000/k1-story.html")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
