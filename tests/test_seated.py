"""The seated story and the renewal rule.

`Take A Seat`'s record 4 is a transient frame, not a rest pose. A seated run is
only safe while that frame keeps being re-commanded: the 55 s hardware test held
because `sit look left/right` re-send it roughly every 6 s. With `autoStand` off
the same pose ended supine on the floor, so this is not a style choice.

Two hardware findings shape the checks:

  1. every narration line in the generated story is followed by a seated look,
     which is also the renewal;
  2. the look must be the fast wording (the standing look's timing bytes), or
     the story spends ~9.4 s per look in silence. With the fast wording it is
     ~1.4 s and the narration has no blanks.

Two direction patterns are validated on hardware: alternating left/right (the
preferred one) and all-left (kept as a variant). Both are checked here so a
regeneration cannot silently lose either shape.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import ROOT, Report  # noqa: E402

LOOKS = {"SpeAction/sit look left", "SpeAction/sit look right"}
SIT = "SpeAction/sit"
SHOW = "SpeAction/sit show hand"
STAND = "SpeAction/stand"
MAX_GAP_S = 5.0     # the tested wait between renewals (55 s hold, 5 s waits)
MAX_SEATED_S = 60.0


def steps(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return data["steps"]


def seated_span(steps: list[dict], clocks: dict[str, float]) -> float:
    """Seconds from the end of `sit` to the start of `stand`."""
    span = 0.0
    inside = False
    for step in steps:
        if step.get("actionPath") == SIT:
            span = 0.0
            inside = True
            continue
        if step.get("actionPath") == STAND:
            inside = False
            continue
        if not inside:
            continue
        if "wait" in step:
            span += float(step["wait"])
        elif "actionPath" in step:
            span += clocks.get(step["actionPath"], 2.0)
    return span


def check_shape(report: Report, story: list[dict]) -> list[str]:
    """Shared checks for any seated routine; returns its look paths."""
    paths = [s["actionPath"] for s in story if "actionPath" in s]
    report.check("starts seated", paths[0] == SIT, paths[0])
    report.check("ends standing", paths[-1] == STAND, paths[-1])
    report.check("only the verified seated scripts are used",
                 set(paths) <= LOOKS | {SIT, STAND, SHOW},
                 str(sorted(set(paths) - LOOKS - {SIT, STAND, SHOW})))

    speaks = [i for i, s in enumerate(story) if "speak" in s]
    looks = [i for i, s in enumerate(story) if s.get("actionPath") in LOOKS]
    renews = [i for i, s in enumerate(story)
              if s.get("actionPath") in LOOKS or s.get("actionPath") == SHOW]
    report.check("every line is followed by a renewal before the next line",
                 all(any(s < r < (speaks[i + 1] if i + 1 < len(speaks) else len(story))
                         for r in renews)
                     for i, s in enumerate(speaks)),
                 f"{len(speaks)} lines, {len(renews)} renewals")
    skipped = [i for i, s in enumerate(speaks)
               if not any(s < look < (speaks[i + 1] if i + 1 < len(speaks) else len(story))
                          for look in looks)]
    first_action_after = (next((x for x in story[speaks[skipped[0]]:] if "actionPath" in x), {})
                          if skipped else {})
    report.check("only the hand-show cue skips the head turn",
                 len(skipped) == 1
                 and first_action_after.get("actionPath") == SHOW,
                 f"{len(speaks)} lines, {len(looks)} looks")

    longest_wait = max((float(s["wait"]) for s in story if "wait" in s), default=0.0)
    report.check(f"no silence longer than {MAX_GAP_S} s", longest_wait <= MAX_GAP_S,
                 f"longest {longest_wait:.2f}s")
    return [s["actionPath"] for s in story if s.get("actionPath") in LOOKS]


def main() -> int:
    report = Report("seated story and renewal rule")
    clocks = {"SpeAction/sit": 7.625, "SpeAction/stand": 10.094,
              "SpeAction/sit look left": 1.375,
              "SpeAction/sit look right": 1.375,
              "SpeAction/sit show hand": 3.578}

    print("\n-- story_seated.json (alternating) --")
    story = steps(ROOT / "routines" / "story_seated.json")
    look_paths = check_shape(report, story)
    want = (["SpeAction/sit look left", "SpeAction/sit look right"]
            * len(look_paths))[:len(look_paths)]
    report.check("head turns alternate left/right", look_paths == want,
                 " ".join(look_paths))
    span = seated_span(story, clocks)
    report.check(f"seated run under {MAX_SEATED_S} s", span <= MAX_SEATED_S, f"{span:.1f}s")
    hand_at = [i for i, s in enumerate(story) if s.get("actionPath") == SHOW]
    speaks_at = [i for i, s in enumerate(story) if "speak" in s]
    report.check("the hand show is in the story once", len(hand_at) == 1,
                 str(hand_at))
    report.check("the hand show sits between the kids' ask and their reaction",
                 len(speaks_at) == 10 and len(hand_at) == 1
                 and speaks_at[7] < hand_at[0] < speaks_at[8])

    print("\n-- story_seated_onedir.json (single direction) --")
    one = steps(ROOT / "routines" / "story_seated_onedir.json")
    one_looks = check_shape(report, one)
    report.check("all head turns look left",
                 set(one_looks) == {"SpeAction/sit look left"},
                 " ".join(one_looks))

    print("\n-- routines/sit.json --")
    short = steps(ROOT / "routines" / "sit.json")
    short_paths = [s["actionPath"] for s in short if "actionPath" in s]
    report.check("starts seated", short_paths[0] == SIT)
    report.check("ends standing", short_paths[-1] == STAND)
    report.check("no idle gap inside the short sequence",
                 not any("wait" in s for s in short))
    report.check("look shows both directions",
                 LOOKS <= set(short_paths), str(sorted(set(short_paths))))

    print("\n-- routines/sit_show_hand.json --")
    hand = steps(ROOT / "routines" / "sit_show_hand.json")
    hand_paths = [s["actionPath"] for s in hand if "actionPath" in s]
    report.check("starts seated", hand_paths[0] == SIT, hand_paths[0])
    report.check("ends standing", hand_paths[-1] == STAND, hand_paths[-1])
    report.check("the hand show is the first beat after sitting",
                 len(hand_paths) > 1 and hand_paths[1] == SHOW, " ".join(hand_paths))
    report.check("only seated moves are played while seated",
                 set(hand_paths[1:-1]) <= LOOKS | {SHOW},
                 str(sorted(set(hand_paths[1:-1]) - LOOKS - {SHOW})))
    show_at = hand_paths.index(SHOW)
    report.check("a renewal look follows the hand show",
                 show_at + 1 < len(hand_paths) - 1
                 and hand_paths[show_at + 1] in LOOKS, " ".join(hand_paths))
    report.check("no idle gap inside the short sequence",
                 not any("wait" in s for s in hand))
    report.check("the hand show gets a generous grace",
                 any(s.get("actionPath") == SHOW
                     and float(s.get("grace", 0)) >= 18 for s in hand))

    print("\n-- story table (tools/story.py) --")
    sys.path.insert(0, str(ROOT / "tools"))
    import story as story_tool  # noqa: E402

    table = story_tool.SEATED
    kinds = [beat[0] for beat in table]
    report.check("table starts with sit and ends with stand",
                 kinds[0] == "actionPath" and table[0][1] == SIT
                 and kinds[-1] == "actionPath" and table[-1][1] == STAND)
    say_at = [i for i, k in enumerate(kinds) if k == "say"]
    report.check("every say is followed by a renew or the hand show",
                 all(i + 1 < len(kinds)
                     and (kinds[i + 1] == "renew"
                          or (kinds[i + 1] == "actionPath" and table[i + 1][1] == SHOW))
                     for i in say_at))
    renew_sides = [table[i][1] for i in range(len(kinds) - 1) if kinds[i] == "renew"]
    report.check("renews alternate left/right",
                 all(a != b for a, b in zip(renew_sides, renew_sides[1:])),
                 " ".join(renew_sides))

    print()
    return report.finish()


if __name__ == "__main__":
    sys.exit(main())
