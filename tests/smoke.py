"""Dry-run a routine: validate it and print every frame it would send.

    python tests/smoke.py routines/hello.json
    python tests/smoke.py routines/hello.json --fast

Needs no hardware and no bleak. It drives the *real* core against the simulator
at high speed, so what you see is exactly what would go over BLE - no separate
copy of the step rules to drift out of sync with the core.
"""
import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from k1lib import protocol, spec  # noqa: E402
from k1lib.bus import SimBus  # noqa: E402
from k1lib.core import RobotCore  # noqa: E402


def describe(frame_hex: str) -> str:
    parsed = protocol.parse(bytes.fromhex(frame_hex))
    if parsed is None:
        return "unparseable"
    label = f"{parsed.type_name:<11}"
    if parsed.type == spec.code("jointMove") and parsed.struct:
        neutral = protocol.joint_struct()
        changed = {k: v for k, v in parsed.struct.items()
                   if k != "speed" and v != neutral.get(k)}
        detail = json.dumps(changed) if changed else "all neutral"
        return f"{label} speed={parsed.struct['speed']:<3} {detail}"
    if parsed.type in (spec.code("action"), spec.code("audio")):
        return f"{label} {parsed.text()}"
    return label.rstrip()


async def run(path: Path, fast: bool, verbose: bool) -> int:
    data = json.loads(path.read_text(encoding="utf-8"))
    steps = data["steps"] if isinstance(data, dict) else data
    print(f"{path}: {len(steps)} steps\n")

    bus = SimBus(speed=60.0 if fast else 1.0)
    core = RobotCore(bus)
    await core.start()

    events: list[tuple[str, dict]] = []
    if verbose:
        core.on(lambda name, data: events.append((name, data)))

    outcome = await core.run("routine", {"steps": steps})
    await core.stop()

    if not outcome.get("ok"):
        print(f"  FAIL {outcome['error']}")
        print(f"\n{'INVALID'}")
        return 1

    step_count = outcome["result"]["steps"]
    print(f"  routine accepted: {step_count} steps played")
    print(f"  frames on the wire: {len(bus.session)}\n")
    for entry in bus.session:
        print(f"    {entry['hex']}  {describe(entry['hex'])}")

    if events:
        print("\n  events:")
        for name, data in events:
            print(f"    {name:<10} {json.dumps(data)}")

    print(f"\n  rejected frames: {len(bus.rejected)}")
    if bus.rejected:
        for hexed in bus.rejected:
            print(f"    {hexed}")

    ok = not bus.rejected and step_count > 0
    print(f"\n{'VALID' if ok else 'INVALID'}")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("file", help="routine json")
    ap.add_argument("--fast", action="store_true", help="run actions at 60x for a quick check")
    ap.add_argument("-v", "--verbose", action="store_true", help="list core events too")
    args = ap.parse_args()
    return asyncio.run(run(Path(args.file), args.fast, args.verbose))


if __name__ == "__main__":
    sys.exit(main())