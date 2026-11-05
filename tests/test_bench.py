"""The action-duration benchmark.

It answers a question `robot.json` cannot: does the robot actually finish each
stunt in the time the vendor declares? The important properties are that the
filters select from the single spec source, that the card adds scripts without
duplicating spec actions, and that a robot which stops reporting cannot wedge
the run or let a measured action be attributed to the wrong name.

The timings here come from `SimBus`, so they prove the measurement plumbing, not
the robot. Real numbers only come from a Windows BLE run.
"""
import asyncio
import csv
import inspect
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import Report  # noqa: E402

from k1lib import bench, sdcard, spec  # noqa: E402
from k1lib.bus import SimBus  # noqa: E402
from k1lib.core import RobotCore  # noqa: E402


def _raises(fn) -> bool:
    """True when `fn` refuses its input instead of guessing."""
    try:
        fn()
    except ValueError:
        return True
    return False


class SilentBus(SimBus):
    """Accepts the action frame and never reports progress, like a stuck robot."""

    async def _run_action(self, path):
        await asyncio.sleep(3600)


class StrayBus(SimBus):
    """Reports 100% without ever reporting the action running: a stale packet.

    This is what the K1 looked like when a 9-second dance "completed" in 47ms.
    """

    async def _run_action(self, path):
        self._emit(self._progress_frame(100))
        await asyncio.sleep(3600)


def make_card(root: Path):
    """A bare directory that passes `looks_like_a_card`, with a few scripts."""
    (root / "AppSysMS").mkdir(parents=True)
    (root / "Warning instructions.txt").write_text("read only\n", encoding="utf-8")
    for folder in ("Action", "ProAction", "SpeAction"):
        (root / folder).mkdir()
    # A spec action under a second folder: must not become a duplicate row.
    (root / "SpeAction" / "Left Punch.sh").write_bytes(b"")
    # A spec name written the way the card writes it (case/punctuation differ).
    (root / "SpeAction" / "bow, please.sh").write_bytes(b"")
    # Not in robot.json: reachable only by path, so no declared clock.
    (root / "SpeAction" / "ZZTestPunch.sh").write_bytes(b"")


async def sim_core(speed: float = 50.0) -> RobotCore:
    core = RobotCore(SimBus(speed=speed))
    core.time_scale = 0.0
    await core.start()
    return core


def main() -> int:
    report = Report("action duration benchmark")
    acts = spec.actions()

    print("\n-- the spec is the default plan --")
    every = bench.entries()
    report.check("one entry per declared action", len(every) == len(acts),
                 f"{len(every)} vs {len(acts)}")
    report.check("every entry carries the spec's clock",
                 all(e.declared_ms == acts[e.name]["duration_ms"] for e in every))
    report.check("paths are the sendable Folder/name form",
                 all(e.path == f"{e.folder}/{e.name}" for e in every))
    report.check("source is spec", all(e.source == "spec" for e in every))
    # Hardware finding (K1-XXXX): the robot.json "neutral" leans right/front,
    # drives rightArm to its minimum, and the robot reported a stuck shoulder.
    # The benchmark must not be able to send it at all.
    report.check("the benchmark cannot write the leaning neutral frame",
                 "stand_between" not in inspect.signature(bench.run).parameters)
    # Hardware finding (K1-XXXX): with no pause between actions only the first
    # of five performed, because progress-100 ends the stream but not the
    # firmware's refusal window. 6s was verified to deliver every action.
    report.check("the default pause is the verified 6s, not zero",
                 inspect.signature(bench.run).parameters["gap"].default
                 == bench.SETTLE_S == 6.0,
                 str(inspect.signature(bench.run).parameters["gap"].default))
    report.check("the plan says a settle pause happens",
                 "settle pause" in bench.describe_plan(every))

    print("\n-- filters are case-insensitive substrings --")
    punches = bench.entries(only="PUNCH")
    names = {e.name for e in punches}
    report.check("only=punch keeps the punches",
                 {"Left Punch", "Right Punch", "Double Punch"} <= names, str(sorted(names)))
    report.check("only=punch drops everything else", "Play Dead" not in names)
    without = bench.entries(skip="punch")
    report.check("skip=punch removes them", not (names & {e.name for e in without}))
    report.check("only and skip are exclusive",
                 len(punches) + len(without) == len(every))
    report.check("multiple terms are comma-separated",
                 {e.name for e in bench.entries(only="play dead,open fire")}
                 == {"Play Dead", "Open Fire"})

    print("\n-- ordering and limit --")
    by_duration = bench.entries(order="duration")
    clocks = [e.declared_ms for e in by_duration]
    report.check("duration order is ascending", clocks == sorted(clocks))
    report.check("the shortest is a punch", by_duration[0].name.endswith("Punch"),
                 by_duration[0].name)
    report.check("the longest is Robot Talk", by_duration[-1].name == "Robot Talk",
                 by_duration[-1].name)
    by_folder = bench.entries(order="folder")
    report.check("folder order groups Action first",
                 by_folder[0].folder == "Action", by_folder[0].folder)
    report.check("limit applies after ordering",
                 [e.name for e in bench.entries(order="duration", limit=3)]
                 == [e.name for e in by_duration[:3]])
    report.check("limit 0 is empty, not everything", bench.entries(limit=0) == [])

    print("\n-- the card adds paths without duplicating the spec --")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "card"
        make_card(root)
        merged = bench.entries(card_root=root)
        paths = [e.path.lower() for e in merged]
        report.check("no duplicate path", len(paths) == len(set(paths)))
        report.check("every card path joins the plan", len(merged) == len(acts) + 3,
                     f"{len(merged)} = {len(acts)} + 3")
        same_name = [e for e in merged if e.name == "Left Punch"]
        report.check("the same stunt in another folder is a separate row",
                     {e.path for e in same_name} ==
                     {"ProAction/Left Punch", "SpeAction/Left Punch"},
                     str([e.path for e in same_name]))
        report.check("a second-folder copy keeps the declared clock",
                     all(e.declared_ms == acts["Left Punch"]["duration_ms"]
                         for e in same_name))
        unknown = [e for e in merged if e.source == "card"]
        report.check("card-only scripts are marked",
                     {e.name for e in unknown} == {"ZZTestPunch"}, str(unknown))
        report.check("card-only scripts have no declared clock",
                     all(e.declared_ms is None for e in unknown))
        report.check("a differently-spelled spec name inherits its clock",
                     any(e.name == "Bow, Please" and e.declared_ms ==
                         acts["Bow, Please"]["duration_ms"] for e in merged))
        report.check("the plan shows '?' for an unknown clock",
                     "?" in bench.describe_plan(unknown))
        bad = Path(tmp) / "not-a-card"
        bad.mkdir()
        try:
            bench.entries(card_root=bad)
            raised = False
        except sdcard.NotACard:
            raised = True
        report.check("a normal folder is rejected, not scanned", raised)

    print("\n-- a simulator run measures each action --")
    core = asyncio.run(sim_core())
    chosen = bench.entries(only="Left Punch,Right Punch,Play Dead,Open Fire",
                           order="name")
    rows = asyncio.run(bench.run(core, chosen, gap=0.0, log=None))
    asyncio.run(core.stop())
    report.check("one row per action", len(rows) == len(chosen), f"{len(rows)}")
    report.check("every row completed", all(r.completed for r in rows),
                 str([r.completion for r in rows]))
    report.check("every row has a firmware clock", all(r.firmware_ms is not None for r in rows))
    report.check("firmware time is not zero", all(r.firmware_ms > 0 for r in rows))
    report.check("waiting is never shorter than progress-100",
                 all(r.measured_ms >= r.firmware_ms for r in rows))
    report.check("the sim is much faster than the declared clock",
                 all(r.ratio is not None and r.ratio < 0.5 for r in rows),
                 str([round(r.ratio or 0, 3) for r in rows]))
    report.check("rows keep the entry's declared clock",
                 all(r.declared_ms == e.declared_ms for r, e in zip(rows, chosen)))
    report.check("the sim reported every action as started",
                 all(r.started is True for r in rows),
                 str([r.started for r in rows]))
    report.check("each row carries its progress trace",
                 all(r.trace and r.trace[-1][1] == 100 for r in rows),
                 str([r.trace for r in rows[:1]]))

    print("\n-- a bad path becomes an error row, not a crash --")
    broken = bench.Entry(name="Broken", path="NoFolder/../Nope",
                         folder="NoFolder", declared_ms=None, source="card")
    broken_core = asyncio.run(sim_core())
    rows = asyncio.run(bench.run(broken_core, [broken], gap=0.0, log=None))
    asyncio.run(broken_core.stop())
    report.check("one row", len(rows) == 1)
    report.check("not ok", rows[0].ok is False)
    report.check("completion is error", rows[0].completion == "error",
                 rows[0].completion)
    report.check("the reason is kept", ".." in (rows[0].error or ""),
                 str(rows[0].error))

    print("\n-- a silent robot stops the run instead of misattributing --")
    silent = RobotCore(SilentBus())
    silent.action_grace_s = 0.1
    silent.action_slack_s = 0.0
    asyncio.run(silent.start())
    two = bench.entries(only="Left Punch,Right Punch")
    rows = asyncio.run(bench.run(silent, two, gap=0.0, max_wait=0.12, log=None))
    report.check("stopped after the first timeout", len(rows) == 1, f"{len(rows)} rows")
    report.check("completion is timeout", rows[0].completion == "timeout",
                 rows[0].completion)
    report.check("no firmware clock was invented", rows[0].firmware_ms is None)
    rows = asyncio.run(bench.run(silent, two, gap=0.0, max_wait=0.12,
                                 stop_on_timeout=False, log=None))
    report.check("keep-going plays both", len(rows) == 2, f"{len(rows)} rows")
    asyncio.run(silent.stop())

    print("\n-- the deadline is duration-aware --")
    core = RobotCore(SimBus())
    report.check("the 81s dance is waited out",
                 core._action_deadline_s(81_000) >= 81,
                 f"{core._action_deadline_s(81_000):.0f}s")
    report.check("long actions get their own clock plus slack",
                 core._action_deadline_s(81_000) == 81 + core.action_slack_s)
    report.check("a per-call grace overrides the whole deadline",
                 core._action_deadline_s(81_000, grace_s=3.0) == 3.0)
    core.action_slack_s = 0.0
    report.check("with no slack, short actions fall back to the grace floor",
                 core._action_deadline_s(800) == core.action_grace_s)
    report.check("with no slack, a long action is its own clock",
                 core._action_deadline_s(20_000) == 20.0)

    print("\n-- a stray 100 with no start is not a completion --")
    # Hardware finding (K1-XXXX): a 9s dance reported progress-100 47ms after
    # the write. The percent packet carries no action id, so a late 100 from the
    # previous stunt used to end the new wait instantly. A completion now needs
    # the robot to have reported the action running first.
    stray = RobotCore(StrayBus())
    stray.action_grace_s = 0.1
    stray.action_slack_s = 0.0
    asyncio.run(stray.start())
    one = bench.entries(only="Funny")
    rows = asyncio.run(bench.run(stray, one, gap=0.0, max_wait=0.25, log=None))
    report.check("an unprovoked 100 is not taken as completion",
                 rows[0].completion == "timeout", rows[0].completion)
    report.check("no timing is invented from it", rows[0].firmware_ms is None,
                 str(rows[0].firmware_ms))
    report.check("the row says it never started", rows[0].started is False,
                 str(rows[0].started))
    stray.require_start_packet = False
    rows = asyncio.run(bench.run(stray, one, gap=0.0, max_wait=0.25, log=None))
    report.check("the old behaviour is still available as an escape hatch",
                 rows[0].completion == "progress-100", rows[0].completion)
    report.check("and then it does produce a (tiny) clock",
                 rows[0].firmware_ms is not None and rows[0].firmware_ms < 50,
                 str(rows[0].firmware_ms))
    # A stray 100 that is rejected must not make the run wait out the full
    # declared clock: silence with no start is the one case `quiet` is for.
    stray.require_start_packet = True
    stray.action_quiet_s = 0.05
    rows = asyncio.run(bench.run(stray, bench.entries(only="Left Punch"),
                                 gap=0.0, log=None))
    report.check("a rejected 100 then silence ends as quiet",
                 rows[0].completion == "quiet", rows[0].completion)
    report.check("and never claims it started", rows[0].started is False)
    asyncio.run(stray.stop())

    print("\n-- a person's verdict can be recorded afterwards --")
    with tempfile.TemporaryDirectory() as tmp:
        jpath = Path(tmp) / "watched.json"
        watched = [
            bench.Row("Left Punch", "ProAction/Left Punch", "ProAction", "spec",
                      800, 700.0, 690.0, "progress-100", 100, True,
                      started=True),
            bench.Row("Boogaloo", "Action/Boogaloo", "Action", "spec",
                      53000, 4600.0, 4650.0, "progress-100", 100, True,
                      started=True),
        ]
        bench.write_json(jpath, watched)
        rows = bench.load_json(jpath)[0]
        report.check("an artifact round-trips into rows", len(rows) == 2)
        bench.mark(jpath, {"left punch": "yes", "boogaloo": "no"})
        rows, payload = bench.load_json(jpath)
        report.check("verdicts are matched on letters and digits",
                     [r.confirmed for r in rows] == ["yes", "no"],
                     str([r.confirmed for r in rows]))
        report.check("the summary counts them",
                     payload["summary"]["confirmed_yes"] == 1
                     and payload["summary"]["confirmed_no"] == 1,
                     str(payload["summary"]))
        report.check("the artifact records when it was annotated",
                     "annotated" in payload)
        table = bench.table(rows)
        report.check("a watched table has a seen column",
                     "seen" in table and "yes" in table, table)
        bench.mark(jpath, {"left punch": "yes"})        # only one of the two
        rows, _ = bench.load_json(jpath)
        table = bench.table(rows)
        report.check("a partly annotated table still prints",
                     "yes" in table and "-" in table, table)

    print("\n-- verdict text survives names that contain commas --")
    got = bench.parse_verdicts("Bow, Please=yes;Kneel,Please=no,Left Punch=yes")
    report.check("a comma inside a name does not split the verdict",
                 got == {"Bow, Please": "yes", "Kneel,Please": "no",
                         "Left Punch": "yes"}, str(got))
    report.check("a value is still checked",
                 _raises(lambda: bench.parse_verdicts("Left Punch=maybe")))
    report.check("a fragment with no value is rejected, not guessed",
                 _raises(lambda: bench.parse_verdicts("Bow, Please")))

    print("\n-- summary and artifacts --")
    mixed = [
        bench.Row("A", "ProAction/A", "ProAction", "spec", 1000, 1100.0, 1000.0,
                  "progress-100", 100, True),
        bench.Row("B", "ProAction/B", "ProAction", "spec", 2000, 2600.0, 2400.0,
                  "progress-100", 100, True),
        bench.Row("C", "ProAction/C", "ProAction", "spec", 500, 120.0, None,
                  "timeout", 50, True),
        bench.Row("D", "SpeAction/D", "SpeAction", "card", None, 0.0, None,
                  "error", None, False, "unknown action"),
    ]
    summary = bench.summarise(mixed)
    report.check("counts every row", summary["actions"] == 4)
    report.check("counts only progress-100 as completed", summary["completed"] == 2)
    report.check("counts timeouts and errors",
                 summary["timeouts"] == 1 and summary["errors"] == 1)
    report.check("median ratio ignores the timeout",
                 summary["ratio_median"] == 1.1, str(summary["ratio_median"]))
    report.check("declared total sums what is known", summary["declared_ms"] == 3500)
    table = bench.table(mixed, summary)
    report.check("table names every action", all(r.name in table for r in mixed))
    report.check("table is worst ratio first", table.index("  B ") < table.index("  A "))

    with tempfile.TemporaryDirectory() as tmp:
        jpath = Path(tmp) / "out" / "bench.json"
        cpath = Path(tmp) / "out" / "bench.csv"
        bench.write_json(jpath, mixed, summary=summary, meta={"bus": "sim"})
        bench.write_csv(cpath, mixed)
        data = json.loads(jpath.read_text())
        report.check("JSON has the summary", data["summary"] == summary)
        report.check("JSON records its provenance", data["bus"] == "sim")
        report.check("JSON row carries the ratio", data["rows"][1]["ratio"] == 1.2)
        report.check("JSON marks completion", data["rows"][2]["completed"] is False)
        with cpath.open(newline="") as fh:
            csv_rows = list(csv.DictReader(fh))
        report.check("CSV has one row per action", len(csv_rows) == 4)
        report.check("CSV columns are the documented set",
                     list(csv_rows[0]) == bench.CSV_FIELDS)
        report.check("CSV keeps the timeout completion",
                     csv_rows[2]["completion"] == "timeout")
        report.check("CSV leaves the missing clock empty",
                     csv_rows[3]["firmware_ms"] == "")

    return report.finish()


if __name__ == "__main__":
    sys.exit(main())
