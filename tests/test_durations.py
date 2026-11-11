"""The declared-duration table.

Two things are worth pinning down here, because both are easy to lose later:

  1. every action has a clock, and it is the same number the core would use;
  2. the table says out loud that it is a declaration, not a measurement.

The second matters more than the first. `robot.json` durations are the vendor's
values; a routine does not pace on them, it waits for `progress-100`. If the
table ever starts reading like a stopwatch, someone will build timing on it and
the narration will drift.
"""
import argparse
import contextlib
import io
import json
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import ROOT, Report  # noqa: E402

import k1ctl  # noqa: E402
from k1lib import spec  # noqa: E402


ROW = re.compile(r"^\s+(\d+\.\d)\s{2,}(.+?)\s{2,}(ProAction|Action)\s", re.M)


def rows(out: str) -> list[tuple[float, str]]:
    """Parse the printed table back into (seconds, action) pairs."""
    return [(float(m.group(1)), m.group(2)) for m in ROW.finditer(out)]


def run_table(**kwargs) -> str:
    args = argparse.Namespace(sort=kwargs.pop("sort", "name"),
                              json=kwargs.pop("json", None), **kwargs)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        k1ctl.list_durations(args)
    return buf.getvalue()


def main() -> int:
    report = Report("declared duration table")
    acts = spec.actions()

    print("\n-- every action has one clock --")
    report.check("actions loaded", len(acts) > 0, f"{len(acts)} actions")
    bad = [n for n, e in acts.items() if not isinstance(e["duration_ms"], int)
           or e["duration_ms"] <= 0]
    report.check("all durations are positive integers", not bad, str(bad[:3]))
    report.check("spec.duration_ms agrees with the table",
                  all(spec.duration_ms(n) == e["duration_ms"] for n, e in acts.items()))

    print("\n-- inside a plausible range --")
    longest = max(e["duration_ms"] for e in acts.values())
    shortest = min(e["duration_ms"] for e in acts.values())
    report.check("nothing shorter than half a second", shortest >= 500,
                 f"{shortest}ms")
    report.check("nothing over two minutes", longest <= 120_000, f"{longest}ms")

    print("\n-- the table names every action exactly once --")
    out = run_table()
    parsed = rows(out)
    report.check("one row per action", len(parsed) == len(acts), f"{len(parsed)} rows")
    report.check("the rows are exactly the spec's actions",
                 {n for _, n in parsed} == set(acts),
                 str(sorted(set(acts) ^ {n for _, n in parsed})[:3]))
    wrong = [(n, secs) for secs, n in parsed
             if secs != round(acts[n]["duration_ms"] / 1000, 1)]
    report.check("each row's duration is the spec's", not wrong, str(wrong[:3]))
    report.check("total matches the sum of the clocks",
                 f"{sum(e['duration_ms'] for e in acts.values()) / 1000:.1f}s" in out)
    report.check("shortest and longest are reported",
                 "\nshortest" in out and "\nlongest" in out)

    print("\n-- it does not pose as a measurement --")
    report.check("says it is not a measurement", "not a measurement" in out)
    report.check("says the routine waits for progress-100",
                 "progress-100" in out)
    report.check("warns that card-only stunts have no clock",
                 "no declared duration" in out)

    print("\n-- sort by duration still covers everything --")
    out = run_table(sort="duration")
    ordered = rows(out)
    report.check("same actions, reordered", {n for _, n in ordered} == set(acts))
    secs = [s for s, _ in ordered]
    report.check("ascending by duration", secs == sorted(secs), str(secs[:4]))
    report.check("starts with a sub-second punch", ordered[0][1].endswith("Punch"),
                 str(ordered[0]))
    report.check("ends with the 81s dance", ordered[-1][1] == "Robot Talk",
                 str(ordered[-1]))

    print("\n-- the JSON artifact round-trips --")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "durations.json"
        run_table(json=str(path))
        report.check("file written", path.exists())
        data = json.loads(path.read_text())
        report.check("all actions present", len(data["actions"]) == len(acts),
                     f"{len(data['actions'])}")
        report.check("total stored", data["total_seconds"] == round(
            sum(e["duration_ms"] for e in acts.values()) / 1000, 2))
        report.check("declares its source", data["source"] == "robot.json")
        report.check("carries the progress-100 caveat",
                     "progress-100" in data["note"])
        report.check("placeholder recorded for card-only stunts",
                     data["unknown_action_ms"] == spec.UNKNOWN_ACTION_MS)
        report.check("paths are sendable", all(
            a["path"] == f"{a['folder']}/{a['action']}" for a in data["actions"]))

    print("\n-- the story routines only use declared, performing actions --")
    routines = {name: ROOT / "routines" / f"{name}.json"
                for name in ("story_first_boot", "story_derelict",
                             "story_derelict_fr", "story_grimm", "story_chat_botte",
                             "story_bremen", "story_corbeau")}
    for rid, routine in routines.items():
        if not routine.exists():
            report.check(f"{rid} routine present", False, str(routine))
            continue
        steps = json.loads(routine.read_text())["steps"]
        named = [s["action"] for s in steps if "action" in s]
        undeclared = [n for n in named if n not in acts]
        report.check(f"{rid}: every stunt has a clock", not undeclared,
                     str(undeclared))
        # `speak` returns as soon as the path is sent, so a narration step with
        # no wait after it would be talked over by the next stunt. This is the
        # bug the story generator exists to prevent, so assert it directly.
        unwaited = [i for i, step in enumerate(steps)
                    if "speak" in step and not
                    (i + 1 < len(steps) and "wait" in steps[i + 1])]
        report.check(f"{rid}: every speak step is followed by a wait", not unwaited,
                     str(unwaited))
        report.check(f"{rid}: every wait is positive",
                     all(float(s["wait"]) > 0 for s in steps if "wait" in s))

    print("\n-- the story is paced on hardware clocks and the 6 s settle --")
    sys.path.insert(0, str(ROOT / "tools"))
    import story as story_tool  # noqa: E402  (tools/ is not a package)

    observed = story_tool.measured_actions()
    secs, src = story_tool.clock_for("Play Dead", observed)
    report.check("a bench row beats the declaration",
                 src == "bench" and secs > 15, f"{src} {secs:.2f}")
    secs, src = story_tool.clock_for("Not A Real Action", observed)
    report.check("an unmeasured name still gets a fallback clock",
                 secs > 0 and src != "bench", f"{src} {secs:.2f}")
    secs, src = story_tool.clock_for("SpeAction/shake head", observed)
    report.check("a card-only path uses its measured clock",
                 src == "bench" and 4.0 < secs < 5.5, f"{src} {secs:.2f}")
    report.check("the settle rule owes the whole 6s from zero",
                 story_tool.settle_needed(0.0) == story_tool.SETTLE_S)
    report.check("narration earns credit toward it",
                 story_tool.settle_needed(3.0) == 3.0)
    report.check("and a long enough gap owes nothing",
                 story_tool.settle_needed(9.0) == 0.0)

    print("\n-- named speakers resolve in the story cast --")
    for key, plan in story_tool.STORIES.items():
        cast = plan.get("cast", {})
        named = [b[2] for b in plan["table"] if len(b) > 2 and b[2]]
        unknown = sorted({who for who in named if who not in cast})
        report.check(f"{key}: every named speaker is in the cast",
                     not unknown, str(unknown))
        if named:
            report.check(f"{key}: the cast really is multiple voices",
                         len({c.get("voice") for c in cast.values()}) >= 2,
                         str(sorted({c.get("voice") for c in cast.values()})))

    clip_ids: dict[str, set[str]] = {}
    for rid, routine in routines.items():
        if not routine.exists():
            continue
        steps = json.loads(routine.read_text())["steps"]
        named = [s["action"] for s in steps if "action" in s]
        named += [s["actionPath"].split("/")[-1] for s in steps if "actionPath" in s]
        clip_ids[rid] = {s["speak"].split("/", 1)[1]
                         for s in steps if "speak" in s}
        # Card-only scripts are addressed by path; robot.json cannot resolve
        # them, and a path the spec already names would be a typo, not a find.
        bad_paths = [s["actionPath"] for s in steps if "actionPath" in s
                     and not re.fullmatch(r"[A-Za-z]+/[^/]+", str(s["actionPath"]))]
        report.check(f"{rid}: card paths are Folder/name", not bad_paths,
                     str(bad_paths))
        spec_paths = {e["path"].lower() for e in spec.actions().values()}
        shadow = [s["actionPath"] for s in steps if "actionPath" in s
                  and s["actionPath"].lower() in spec_paths]
        report.check(f"{rid}: card paths are not spec names", not shadow, str(shadow))
        for banned in ("Lay Face Down", "Funny", "Easy Action", "Quick sneak",
                       "The Squat"):
            report.check(f"{rid} avoids {banned}", banned not in named)
        # Walk the routine with the same clocks the generator used, and assert
        # that no action starts within SETTLE_S of the previous one ending.
        at, last_end, worst = 0.0, None, None
        for step in steps:
            if "wait" in step:
                at += float(step["wait"])
            elif "action" in step or "actionPath" in step:
                key = step.get("action", step.get("actionPath"))
                if last_end is not None:
                    gap = at - last_end
                    worst = gap if worst is None else min(worst, gap)
                at += story_tool.clock_for(key, observed)[0]
                last_end = at
        report.check(f"{rid}: no stunt follows the last one inside 6s",
                     worst is not None and worst >= story_tool.SETTLE_S - 0.06,
                     f"smallest gap {worst}")
        # A step's default deadline is the declared clock + slack; Play Dead is
        # declared 1.68s and runs 16.78s, so without a per-step grace the routine
        # would stop 0.1s before the stunt ends.
        short = [step.get("action", step.get("actionPath")) for step in steps
                 if ("action" in step or "actionPath" in step)
                 and float(step.get("grace", 0)) <=
                 story_tool.clock_for(step.get("action", step.get("actionPath")),
                                      observed)[0]]
        report.check(f"{rid}: every stunt may run past its measured clock",
                     not short, str(short))

    # The card's 02/ is one flat folder, so the stories must use disjoint ids or
    # building one would overwrite another's narration.
    if len(clip_ids) > 1:
        names = list(clip_ids)
        clashes = [(a, b, sorted(clip_ids[a] & clip_ids[b]))
                   for i, a in enumerate(names) for b in names[i + 1:]
                   if clip_ids[a] & clip_ids[b]]
        report.check("the stories never share a clip id", not clashes,
                     str(clashes))

    return report.finish()


if __name__ == "__main__":
    sys.exit(main())
