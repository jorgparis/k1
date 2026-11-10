"""The phone player: one self-contained page, and bytes that match k1lib.

The page is only useful if it can be trusted before it is opened on a phone, so
this suite checks the things that would otherwise be found by watching a robot
do the wrong thing:

  * the embedded routine steps are exactly `routines/story_*.json`;
  * every action name has a spec path, and every clip is a numeric 02/ id;
  * the frame encoder produces the same bytes as `k1lib.protocol.packet`
    (checked by running the same JS in Node);
  * the page makes no network request and has no external asset;
  * the all-important split notification reassembly works (two frames in one
    notification, one frame across many);
  * the HTML is deterministic, so "which page did I copy?" has an answer.
"""
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import ROOT, Report  # noqa: E402

sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tools" / "phone"))

import phone  # noqa: E402
import story as story_plan  # noqa: E402
from k1lib import protocol, spec  # noqa: E402

VECTORS = [
    ("action Say Hello", "action", "ProAction/Say Hello"),
    ("action Bow, Please", "action", "ProAction/Bow, Please"),
    ("action Do Cat Meows", "action", "ProAction/Do Cat Meows"),
    ("action Start Shooting", "action", "ProAction/Start Shooting"),
    ("audio 401", "audio", "02/401"),
    ("audio 518", "audio", "02/518"),
    ("autoStand on", "autoStand", True),
    ("stop", "stop", None),
]


def embedded_data(html: str) -> dict:
    marker = "window.K1_DATA = "
    start = html.index(marker) + len(marker)
    end = html.index(";\n</script>", start)
    return json.loads(html[start:end])


def script_blocks(html: str) -> list[str]:
    return re.findall(r"<script>(.*?)</script>", html, re.S)


def main() -> int:
    report = Report("phone player")
    tmp = Path(tempfile.mkdtemp(prefix="k1-phone-"))
    try:
        out = tmp / "k1-story.html"
        built = phone.build(out)
        html = out.read_text(encoding="utf-8")
        data = embedded_data(html)

        print("\n-- the page is standalone --")
        report.check("built", out.exists() and built["bytes"] > 20_000,
                     f"{built['bytes']} bytes")
        report.check("no <script src=", "<script src=" not in html)
        report.check("no <link>", "<link" not in html.lower())
        report.check("no external URL", not re.search(r"https?://(?!localhost)", html))
        report.check("no fetch / XHR / websocket",
                     not any(t in html for t in ("fetch(", "XMLHttpRequest", "new WebSocket")))
        report.check("no <img>", "<img" not in html.lower())

        ids = set(re.findall(r'id="([^"]+)"', html))
        wanted = set(re.findall(r'\$\("([^"]+)"\)', (ROOT / "tools" / "phone" / "player.js").read_text()))
        report.check("every element the player looks up exists", wanted <= ids,
                     str(sorted(wanted - ids)))

        print("\n-- the BLE endpoints are the spec's --")
        report.check("service uuid", data["service"] == spec.service_uuid(),
                     data["service"])
        report.check("characteristic uuid",
                     data["characteristic"] == spec.characteristic_uuid(),
                     data["characteristic"])
        report.check("every spec action has a path",
                     data["actions"] == {n: e["path"] for n, e in spec.actions().items()})

        print("\n-- the routines are the ones k1ctl plays --")
        routines = sorted((ROOT / "routines").glob("story_*.json"))
        ids = {p.stem.removeprefix("story_") for p in routines}
        report.check("every story table has a routine",
                     set(story_plan.STORIES) <= ids,
                     str(sorted(set(story_plan.STORIES) - ids)))
        report.check("the page embeds exactly those stories",
                     {s["id"] for s in data["stories"]} == set(story_plan.STORIES),
                     str(sorted({s["id"] for s in data["stories"]}
                                ^ set(story_plan.STORIES))))

        for story in data["stories"]:
            rid = story["id"]
            source = json.loads((ROOT / "routines" / f"story_{rid}.json").read_text())
            steps = story["steps"]
            report.check(f"{rid}: steps are byte-identical to the routine",
                         steps == source["steps"])
            report.check(f"{rid}: one note per step", len(story["notes"]) == len(steps),
                         f"{len(story['notes'])} vs {len(steps)}")
            note_bad = [i for i, s in enumerate(steps)
                        if ("action" in s or "actionPath" in s)
                        and story["notes"][i] != s.get("action", s.get("actionPath"))]
            report.check(f"{rid}: every stunt is captioned with its name", not note_bad,
                         str(note_bad[:3]))
            speak_bad = [i for i, s in enumerate(steps)
                         if "speak" in s and not story["notes"][i]]
            report.check(f"{rid}: every clip has a caption", not speak_bad, str(speak_bad[:3]))
            report.check(f"{rid}: waits carry no caption",
                         all(story["notes"][i] is None
                             for i, s in enumerate(steps) if "wait" in s))

            bad_paths = [s["action"] for s in steps
                         if "action" in s and data["actions"].get(s["action"]) !=
                         spec.actions()[s["action"]]["path"]]
            report.check(f"{rid}: every stunt resolves to its spec path", not bad_paths,
                         str(bad_paths))
            bad_cards = [s["actionPath"] for s in steps
                         if "actionPath" in s and
                         not re.fullmatch(r"[A-Za-z]+/[^/]+", str(s["actionPath"]))]
            report.check(f"{rid}: every card path is Folder/name", not bad_cards,
                         str(bad_cards))
            # A card path is card-only by definition; one that is also in the
            # spec means the beat should have used `action` instead.
            shadowed = [s["actionPath"] for s in steps if "actionPath" in s
                        and s["actionPath"].lower() in
                        {p.lower() for p in data["actions"].values()}]
            report.check(f"{rid}: card paths stay outside the spec", not shadowed,
                         str(shadowed))
            bad_clips = [s["speak"] for s in steps
                         if "speak" in s and not re.fullmatch(r"02/\d+", s["speak"])]
            report.check(f"{rid}: every clip is a numeric 02/ id", not bad_clips,
                         str(bad_clips))
            report.check(f"{rid}: every stunt has a grace",
                         all(s.get("grace") for s in steps
                             if "action" in s or "actionPath" in s))

            recomputed = phone.expected_seconds(steps)
            report.check(f"{rid}: expected duration is the routine's",
                         abs(story["expected"] - round(recomputed, 2)) < 0.01,
                         f"{story['expected']} vs {recomputed:.2f}")
            gaps = sum(phone.GAP_S for s in steps
                       if "speak" in s or "action" in s or "actionPath" in s)
            preview = story["preview_seconds"]
            report.check(f"{rid}: rehearsal clocks agree with the preview",
                         abs(story["expected"] - gaps - preview) < 1.5,
                         f"{story['expected'] - gaps:.1f}s vs preview {preview}s")
            report.check(f"{rid}: counts add up",
                         story["stunt_count"] == sum(
                             1 for s in steps if "action" in s or "actionPath" in s)
                         and story["clip_count"] == sum(1 for s in steps if "speak" in s))

        print("\n-- the moves menu covers everything the phone can play --")
        moves = data["moves"]
        move_paths = [m["path"] for m in moves]
        report.check("every spec action is in the menu",
                     set(data["actions"].values()) <= set(move_paths),
                     str(sorted(set(data["actions"].values()) - set(move_paths))))
        report.check("no duplicate move paths",
                     len(move_paths) == len(set(move_paths)),
                     f"{len(move_paths)} vs {len(set(move_paths))}")
        report.check("menu names are unique",
                     len({m["name"] for m in moves}) == len(moves))
        bad_paths = [m["path"] for m in moves
                     if not re.fullmatch(r"[A-Za-z]+/[^/]+", str(m["path"]))]
        report.check("every move path is Folder/name", not bad_paths,
                     str(bad_paths))
        report.check("every move has a positive grace",
                     all(float(m.get("grace", 0)) > 0 for m in moves))
        report.check("unknown clocks stay null instead of a guess",
                     all(m["seconds"] is None or float(m["seconds"]) > 0
                         for m in moves))
        report.check("the menu groups are known",
                     {m["group"] for m in moves} <= {"Card", "Action", "ProAction"},
                     str(sorted({m["group"] for m in moves})))
        card = {m["path"]: m for m in moves if m["group"] == "Card"}
        wanted = {"SpeAction/sit", "SpeAction/stand", "SpeAction/sit look left",
                  "SpeAction/sit look right", "SpeAction/sit show hand",
                  "SpeAction/look left",
                  "SpeAction/look right", "SpeAction/wave left",
                  "SpeAction/wave right", "SpeAction/shake head"}
        report.check("the card-only moves are in the menu", wanted <= set(card),
                     str(sorted(wanted - set(card))))
        report.check("the seated moves warn that they leave it seated",
                     all("seated" in card[p].get("note", "")
                         for p in ("SpeAction/sit", "SpeAction/sit look left",
                                   "SpeAction/sit look right",
                                   "SpeAction/sit show hand")))
        report.check("the watched hand show carries its measured clock",
                     card["SpeAction/sit show hand"]["seconds"] is not None
                     and card["SpeAction/sit show hand"]["source"] == "measured"
                     and 3.0 < card["SpeAction/sit show hand"]["seconds"] < 5.0,
                     str(card["SpeAction/sit show hand"]))
        report.check("the menu never shadows a spec path",
                     all(m["path"].lower() not in
                         {p.lower() for p in data["actions"].values()}
                         for m in moves if m["group"] == "Card"))

        print("\n-- encoder matches k1lib byte for byte --")
        vectors = [{"name": name, "type": kind, "payload": payload,
                    "hex": protocol.packet(spec.code(kind), payload).hex(),
                    "code": int(spec.code(kind), 16)}
                   for name, kind, payload in VECTORS]
        vec_file = tmp / "vectors.json"
        vec_file.write_text(json.dumps(vectors), encoding="utf-8")
        node = shutil.which("node")
        if not node:
            print("SKIP  node is not installed; the JS encoder was not exercised")
        else:
            result = subprocess.run(
                [node, str(ROOT / "tools" / "phone" / "check_frames.js"), str(vec_file)],
                capture_output=True, text=True)
            tail = (result.stdout.strip().splitlines() or ["(no output)"])[-1]
            report.check("node frame + stream vectors", result.returncode == 0, tail)
            if result.returncode != 0:
                print(result.stdout[-2000:])
                print(result.stderr[-2000:])

            print("\n-- the inline scripts parse --")
            for i, body in enumerate(script_blocks(html), 1):
                js = tmp / f"script{i}.js"
                js.write_text(body, encoding="utf-8")
                check = subprocess.run([node, "--check", str(js)],
                                       capture_output=True, text=True)
                report.check(f"script block {i} parses", check.returncode == 0,
                             check.stderr.strip().splitlines()[-1] if check.stderr else "")

            print("\n-- the player runs against a fake robot --")
            result = subprocess.run(
                [node, str(ROOT / "tools" / "phone" / "check_player.js")],
                capture_output=True, text=True, timeout=120)
            tail = (result.stdout.strip().splitlines() or ["(no output)"])[-1]
            report.check("connect / progress / fallback / rehearsal / stop",
                         result.returncode == 0, tail)
            if result.returncode != 0:
                print(result.stdout[-4000:])
                print(result.stderr[-4000:])

        print("\n-- the page is deterministic --")
        out2 = tmp / "again.html"
        phone.build(out2)
        report.check("two builds are identical",
                     out2.read_bytes() == out.read_bytes())
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    return report.finish()


if __name__ == "__main__":
    sys.exit(main())
