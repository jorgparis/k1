#!/usr/bin/env python3
"""k1ctl - drive the Robosen Interstellar Scout K1.

Real robot (Bluetooth LE):
    python k1ctl.py scan
    python k1ctl.py info
    python k1ctl.py do "Left Punch" "Cyberpunk"
    python k1ctl.py walk forward 1500
    python k1ctl.py head 40
    python k1ctl.py stand --allow-leaning-neutral   # vendor neutral: it leans
    python k1ctl.py routine routines/hello.json
    python k1ctl.py bench --only "Left Punch"       # time one action
    python k1ctl.py verdict run.json --set "Left Punch=yes"

Simulator - nothing plugged in, identical code path:
    python k1ctl.py --sim info
    python k1ctl.py --sim routine routines/hello.json
    python k1ctl.py --sim record routines/hello.json session.json

Offline:
    python k1ctl.py actions
    python k1ctl.py joints
    python k1ctl.py encode head=160
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

# A Windows console defaults to cp1252, which cannot encode every character in
# a robot or action name. Print UTF-8 with replacements rather than crashing.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

from k1lib import bench, protocol, sdcard, spec  # noqa: E402
from k1lib.bus import NotFound, SimBus, scan_once  # noqa: E402
from k1lib.core import DIRECTIONS, IntentError, RobotCore  # noqa: E402
from k1lib.robot import K1  # noqa: E402


# --------------------------------------------------------------------------
# offline commands
# --------------------------------------------------------------------------
def list_actions():
    acts = spec.actions()
    print(f"{len(acts)} actions\n")
    for group, label in (("ProAction", "stunts"), ("Action", "dances")):
        names = sorted(n for n, e in acts.items() if e["folder"] == group)
        print(f"--- {label} ({group}) ---")
        for i in range(0, len(names), 3):
            row = [f"{n} ({acts[n]['duration_ms'] / 1000:.1f}s)" for n in names[i : i + 3]]
            print("  " + "".join(c.ljust(32) for c in row).rstrip())
        print()


def list_safe():
    """What the name-based classifier guesses, shortest first.

    Not a permission list. Every factory action is safe to play; this is the
    guess, reported so it can be judged rather than obeyed.
    """
    acts = spec.actions()
    risky = spec.fall_risk()
    safe = spec.safe_actions()
    print(f"{len(acts)} actions, all factory trajectories and all safe to play")
    print("the classifier below is a guess from action names, not a veto\n")
    print(f"name suggests upright ({len(safe)}):")
    for i in range(0, len(safe), 3):
        row = [f"{n} ({acts[n]['duration_ms'] / 1000:.1f}s)" for n in safe[i : i + 3]]
        print("  " + "".join(c.ljust(34) for c in row).rstrip())
    print(f"\nname suggests prone or inverted ({len(risky)}) - in brackets, the word matched:")
    blocked = sorted(risky.items())
    for i in range(0, len(blocked), 3):
        row = blocked[i : i + 3]
        print("  " + "".join(f"{n} <{w}>".ljust(32) for n, w in row).rstrip())


def list_durations(args):
    """Every declared action with its clock, for planning a routine by hand.

    These are the vendor's numbers from robot.json, not measurements. The core
    does not pace on them: it holds the queue until the robot reports
    progress-100, so this table answers "how long should I leave between beats"
    while the robot itself decides when a stunt is actually over.
    """
    acts = spec.actions()
    rows = sorted(acts.items())

    groups = {}
    for name, e in rows:
        groups.setdefault(e["folder"], []).append((name, e["duration_ms"],
                                                   spec.classify_fall_risk(name)))

    total = sum(e["duration_ms"] for e in acts.values()) / 1000

    print(f"{len(acts)} actions, {total:.1f}s of declared animation in total")
    print("robot.json duration_ms - the vendor's clock, not a measurement.")
    print("the routine waits for progress-100, so treat these as planning figures.\n")

    if args.sort == "duration":
        flat = sorted(((ms, name, folder, word)
                       for folder, names in groups.items()
                       for name, ms, word in names))
        print(f"  {'dur':>6}  {'action':<30} {'folder':<9} {'risk':<8} description")
        print(f"  {'-' * 6}  {'-' * 30} {'-' * 9} {'-' * 8} {'-' * 24}")
        for ms, name, folder, word in flat:
            print(f"  {ms / 1000:6.1f}  {name:<30} {folder:<9} "
                  f"{('<' + word + '>') if word else '-':<8} "
                  f"{acts[name].get('description', '') or ''}")
        print()
    else:
        print(f"  {'dur':>6}  {'action':<30} {'folder':<9} {'risk':<8} description")
        print(f"  {'-' * 6}  {'-' * 30} {'-' * 9} {'-' * 8} {'-' * 24}")
        for folder, names in groups.items():
            subtotal = sum(r[1] for r in names) / 1000
            print(f"  -- {folder} ({len(names)} actions, {subtotal:.1f}s)")
            for name, ms, word in names:
                print(f"  {ms / 1000:6.1f}  {name:<30} {folder:<9} "
                      f"{('<' + word + '>') if word else '-':<8} "
                      f"{acts[name].get('description', '') or ''}")
            print()

    fastest = min(rows, key=lambda r: r[1]["duration_ms"])
    slowest = max(rows, key=lambda r: r[1]["duration_ms"])
    print(f"shortest  {fastest[1]['duration_ms'] / 1000:5.1f}s  {fastest[0]}")
    print(f"longest   {slowest[1]['duration_ms'] / 1000:5.1f}s  {slowest[0]}")
    print("\nrisk is the name-based guess in angle brackets, not a veto - every")
    print("factory action here is a vendor trajectory. Actions that exist only on")
    print("the SD card have no declared duration at all; the library uses 2.0s as a")
    print("deadline for them and still waits for the real progress signal.")

    if args.json:
        out = Path(args.json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({
            "source": "robot.json",
            "note": "declared durations, not measured; routines wait for progress-100",
            "unknown_action_ms": spec.UNKNOWN_ACTION_MS,
            "total_seconds": round(total, 2),
            "actions": [
                {"action": n, "folder": e["folder"], "path": e["path"],
                 "duration_ms": e["duration_ms"],
                 "description": e.get("description", ""),
                 "risk_guess": spec.classify_fall_risk(n)}
                for n, e in sorted(acts.items())
            ],
        }, indent=2) + "\n", encoding="utf-8")
        print(f"\nwrote {out}")


def list_joints():
    print(f"{'joint':16} {'idx':>3} {'neutral':>8} {'min':>5} {'max':>5}")
    for name, e in spec.joints().items():
        print(f"{name:16} {e['index']:>3} {e['neutral']:>8} {e['min']:>5} {e['max']:>5}")
    s = spec.speed()
    print(f"{'speed':16} {s['index']:>3} {s['neutral']:>8} {s['min']:>5} {s['max']:>5}")


def cmd_encode(args):
    pose = {}
    for item in args.pose:
        key, _, val = item.partition("=")
        pose[key] = int(val)
    pose = protocol.clamp_pose(pose)
    frame = protocol.packet(spec.code("jointMove"), protocol.joint_struct(pose, speed=args.speed))
    print(f"pose   {json.dumps(pose)}")
    print(f"bytes  {frame.hex()}")
    print(f"check  {'ok' if protocol.parse(frame).valid else 'BAD'}")


def cmd_verdict(args):
    """Attach what a person saw to a finished run, without touching the robot."""
    verdicts = {}
    try:
        verdicts = bench.parse_verdicts(args.verdicts)
    except ValueError as exc:
        print(f"bad verdict {str(exc)!r}; use NAME=yes|no|unsure", file=sys.stderr)
        return 2
    if not verdicts:
        print("no verdicts given", file=sys.stderr)
        return 2
    try:
        rows = bench.mark(args.file, verdicts, csv_path=args.csv)
    except FileNotFoundError:
        print(f"no such run artifact: {args.file}", file=sys.stderr)
        return 2
    print(bench.table(rows))
    unmatched = [k for k in verdicts
                 if not any(bench.norm(k) in bench.norm(r.path) for r in rows)]
    if unmatched:
        print(f"warning: matched no row: {', '.join(unmatched)}", file=sys.stderr)
    print(f"\nannotated {args.file}")
    return 0


def cmd_scan(args):
    found = asyncio.run(scan_once(args.name))
    if not found:
        print("no K1 found. Power it on and close the K One app.", file=sys.stderr)
        return 1
    for addr, nm, rssi in found:
        print(f"{nm:20} {addr}  rssi={rssi}")
    return 0


def cmd_daemon(args):
    """Run the core as a JSON-RPC server so other processes can drive it."""
    from k1lib.core import make_bus
    from k1lib.wire import serve_stdio

    bus_kind = "sim" if args.sim else "ble"
    print(f"daemon: bus={bus_kind} rpc={args.rpc}", file=sys.stderr)
    try:
        asyncio.run(serve_stdio(RobotCore(make_bus(bus_kind, args.name))))
    except KeyboardInterrupt:
        return 130
    return 0


# --------------------------------------------------------------------------
# robot commands
# --------------------------------------------------------------------------
def load_steps(path: str):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return data["steps"] if isinstance(data, dict) else data


async def run_robot(args):
    bus_kind = "sim" if args.sim else "ble"
    k1 = K1(bus_kind, name_contains=args.name, auto_stand=not args.no_auto_stand)
    label = await k1.connect()
    print(f"connected: {label}" + ("  [SIMULATED]" if k1.simulated else ""), file=sys.stderr)

    if args.verbose:
        k1.on(lambda event, data: print(f"  <- {event} {json.dumps(data)}", file=sys.stderr))

    try:
        if args.cmd == "info":
            print(json.dumps(await k1.info(), indent=2))
            print(json.dumps(await k1.state(), indent=2))
        elif args.cmd == "do":
            for name in args.names:
                print(await k1.core.i_action(name, strict=args.strict,
                                             allow_fall_risk=args.allow_fall_risk))
        elif args.cmd == "walk":
            print("note: a move drops the robot into a locomotion stance - it "
                  "bends its knees and lowers its body. Not a subtle motion. "
                  "Roughly the first 1000ms is stance transition.",
                  file=sys.stderr)
            print(await k1.move(args.direction, args.ms))
        elif args.cmd == "audio":
            report = await k1.core.i_audio()
            print(f"declared in robot.json : {report['declared_count']}")
            print(f"reported by the robot  : {report['on_device_count']}")
            print(f"list confirmed complete: {report['list_complete']}")
            if report["not_in_spec"]:
                print(f"\non the robot but NOT in robot.json ({len(report['not_in_spec'])}):")
                for name in report["not_in_spec"]:
                    print(f"  {name}")
            else:
                print("\nnothing on the robot that robot.json does not declare")
        elif args.cmd == "folders":
            report = await k1.core.i_folders()
            print(f"folders: {report['folders']} "
                  f"(complete={report['folders_complete']})")
            print(f"{report['total_audio']} audio entries\n")
            for folder, names in report["audio"].items():
                print(f"  {folder}/  ({len(names)})")
                for i in range(0, min(len(names), 24), 6):
                    print("    " + "".join(n.ljust(16) for n in names[i:i + 6]).rstrip())
                if len(names) > 24:
                    print(f"    ... and {len(names) - 24} more")
        elif args.cmd == "speak":
            spoken = await k1.core.i_speak(args.path)
            print(spoken)
            for key in ("naming_advice", "advice"):
                if spoken.get(key):
                    print(spoken[key], file=sys.stderr)
        elif args.cmd == "clipinfo":
            print(await k1.core.i_clip_info(args.clip))
        elif args.cmd == "head":
            print(await k1.head(args.degrees))
        elif args.cmd == "glide":
            print(await k1.glide(json.loads(args.pose) if args.pose else None,
                                 speed=args.speed, max_step=args.max_step))
        elif args.cmd == "readpose":
            print(json.dumps(await k1.read_pose(), indent=2))
        elif args.cmd == "stand":
            print(await k1.stand(allow_leaning_neutral=args.allow_leaning_neutral))
        elif args.cmd == "volume":
            print(await k1.volume(args.level))
        elif args.cmd == "routine":
            print(await k1.routine(load_steps(args.file)))
        elif args.cmd == "record":
            print(await k1.routine(load_steps(args.file)))
            bus = k1.bus
            if isinstance(bus, SimBus):
                bus.save(args.out)
                print(f"recorded {len(bus.session)} frames -> {args.out}", file=sys.stderr)
                if bus.rejected:
                    print(f"WARNING: {len(bus.rejected)} rejected frames", file=sys.stderr)
        elif args.cmd == "bench":
            rows = args.bench_entries
            if not k1.simulated:
                print("the robot will play every action in the plan, one at a "
                      "time. Clear the floor: flips and lie-downs need room. "
                      "No neutral joint frame is sent between actions.",
                      file=sys.stderr)
            print(bench.describe_plan(rows), file=sys.stderr)
            print(file=sys.stderr)
            results = await bench.run(
                k1.core, rows,
                gap=args.gap,
                max_wait=args.max_wait,
                stop_on_timeout=not args.keep_going,
            )
            summary = bench.summarise(results)
            print()
            print(bench.table(results, summary))
            meta = {
                "bus": "sim" if k1.simulated else "ble",
                "robot": label,
                "gap_s": args.gap,
                "max_wait_s": args.max_wait,
                "card_root": str(args.card_root) if args.card_root else None,
            }
            if args.json:
                print(f"\nwrote {bench.write_json(args.json, results, summary=summary, meta=meta)}")
            if args.csv:
                print(f"wrote {bench.write_csv(args.csv, results)}")
    finally:
        await k1.close()
    return 0


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("-v", "--verbose", action="store_true", help="log packets and intents")
    ap.add_argument("--sim", action="store_true", help="use the simulator instead of BLE")
    ap.add_argument("--name", default="K1", help="BLE name fragment to search for")
    ap.add_argument("--no-auto-stand", action="store_true",
                    help="do not enable autoStand on connect (safer for probing)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("scan", help="look for a K1 over BLE")
    sub.add_parser("info", help="print battery + state")
    sub.add_parser("actions", help="list every stunt and dance")
    p = sub.add_parser("durations", help="table of every action with its declared clock")
    p.add_argument("--sort", choices=["name", "duration"], default="name")
    p.add_argument("--json", metavar="PATH", help="also write the table as JSON")
    sub.add_parser("safe", help="show the name-based classifier (informational; factory actions are safe)")
    sub.add_parser("joints", help="list joint limits and neutrals")
    p = sub.add_parser("stand", help="write robot.json's neutral frame (leans; "
                                     "refused unless forced)")
    p.add_argument("--allow-leaning-neutral", action="store_true",
                   help="really write the vendor neutral; it leans right/forward "
                        "and has tripped a stuck-shoulder report on hardware")

    p = sub.add_parser("do"); p.add_argument("names", nargs="+")
    p.add_argument("--strict", action="store_true",
                   help="refuse name-matched fall-risk stunts (they are allowed by "
                        "default, since they are vendor trajectories)")
    p.add_argument("--allow-fall-risk", action="store_true",
                   help="permit them even under --strict")
    p = sub.add_parser("walk"); p.add_argument("direction", choices=sorted(DIRECTIONS))
    p.add_argument("--ms", type=int, default=4000,
                   help="total move time; ~1000ms of it is spent entering the "
                        "locomotion stance before it travels (default 4000)")
    p = sub.add_parser("head"); p.add_argument("degrees", type=int)
    p = sub.add_parser("glide", help="ease to a pose over several frames (gentler on servos)")
    p.add_argument("pose", nargs="*")
    p.add_argument("--speed", type=int, default=30)
    p.add_argument("--max-step", type=int, default=8,
                   help="largest per-joint change allowed in one frame, in counts")
    p = sub.add_parser("readpose", help="ask the robot for its joint positions (unverified path)")
    p = sub.add_parser("volume"); p.add_argument("level", type=int)
    p = sub.add_parser("audio", help="what the robot says it can play")
    p = sub.add_parser("folders", help="audio folders and clips under each")
    p = sub.add_parser("speak", help="play your own audio by on-device path")
    p.add_argument("path", help=f'e.g. "{spec.USER_AUDIO_FOLDER}/story01" '
                                f"(file is {spec.USER_AUDIO_FOLDER}/story01"
                                f"{spec.audio_extension()})")
    p = sub.add_parser("clipinfo", help="probe the `play` command (unverified)")
    p.add_argument("clip", type=int, nargs="?", default=101)
    p = sub.add_parser("card", help="read-only inventory of the robot's SD card")
    p.add_argument("path", nargs="?", default="D:/", help="Windows path to the card")
    p = sub.add_parser("routine"); p.add_argument("file")
    p = sub.add_parser("record", help="run a routine on the simulator and save the frames")
    p.add_argument("file"); p.add_argument("out")
    p = sub.add_parser("bench", help="play every action and measure its real duration")
    p.add_argument("--dry-run", action="store_true",
                   help="list what would be played, without connecting to the robot")
    p.add_argument("--only", metavar="TEXT",
                   help="comma-separated names or substrings to include")
    p.add_argument("--skip", metavar="TEXT",
                   help="comma-separated names or substrings to leave out")
    p.add_argument("--limit", type=int, metavar="N", help="play at most N actions")
    p.add_argument("--order", choices=["name", "duration", "folder"], default="name")
    p.add_argument("--card-root", metavar="PATH",
                   help="also play every .sh on the SD card (needs the card mounted)")
    p.add_argument("--gap", type=float, default=bench.SETTLE_S, metavar="SECONDS",
                   help=f"pause after each action (default {bench.SETTLE_S:.0f}s). "
                        "Not cosmetic: with no pause the firmware drops the next "
                        "action. Never inserts a neutral joint frame")
    p.add_argument("--max-wait", type=float, default=None, metavar="SECONDS",
                   help="hard completion cap per action; overrides the "
                        "duration-aware deadline")
    p.add_argument("--keep-going", action="store_true",
                   help="continue after a timeout instead of stopping")
    p.add_argument("--json", metavar="PATH", help="write full results as JSON")
    p.add_argument("--csv", metavar="PATH", help="write full results as CSV")

    p = sub.add_parser("verdict",
                       help="record what you actually saw, into a finished run")
    p.add_argument("file", metavar="JSON", help="a --json artifact from `bench`")
    p.add_argument("--set", dest="verdicts", required=True, metavar="NAME=yes,...",
                   help="e.g. 'Left Punch=yes,Boogaloo=no' (yes/no/unsure)")
    p.add_argument("--csv", metavar="PATH", help="also rewrite a CSV beside it")
    p = sub.add_parser("encode"); p.add_argument("pose", nargs="*")
    p.add_argument("--speed", type=int, default=40)
    p = sub.add_parser("daemon", help="serve JSON-RPC 2.0 over stdio (same as python -m k1lib.wire)")
    p.add_argument("--rpc", default="stdio", choices=["stdio"],
                   help="transport for the daemon (add unix/tcp here for remote clients)")

    args = ap.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING,
                        format="%(name)s %(message)s")

    try:
        if args.cmd == "scan":
            return cmd_scan(args)
        if args.cmd == "card":
            from k1lib import sdcard
            try:
                print(sdcard.describe(args.path))
            except sdcard.NotACard as exc:
                print(f"not a K1 card: {exc}", file=sys.stderr)
                return 1
            return 0
        if args.cmd == "actions":
            list_actions(); return 0
        if args.cmd == "durations":
            list_durations(args); return 0
        if args.cmd == "safe":
            list_safe(); return 0
        if args.cmd == "joints":
            list_joints(); return 0
        if args.cmd == "encode":
            cmd_encode(args); return 0
        if args.cmd == "daemon":
            return cmd_daemon(args)
        if args.cmd == "verdict":
            return cmd_verdict(args)
        if args.cmd == "bench":
            try:
                args.bench_entries = bench.entries(
                    args.card_root, only=args.only, skip=args.skip,
                    order=args.order, limit=args.limit,
                )
            except sdcard.NotACard as exc:
                print(f"not a K1 card: {exc}", file=sys.stderr)
                return 1
            if not args.bench_entries:
                print("nothing matched; check --only / --skip / --limit", file=sys.stderr)
                return 2
            if args.dry_run:
                print(bench.describe_plan(args.bench_entries))
                return 0
        return asyncio.run(run_robot(args))
    except IntentError as exc:
        print(f"intent error: {exc}", file=sys.stderr); return 2
    except NotFound as exc:
        print(exc, file=sys.stderr); return 3
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())