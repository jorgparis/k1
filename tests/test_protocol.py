"""Spec and codec invariants.

test_vectors.py proves the bytes match the JS oracle. This proves the *spec*
itself is coherent, and that the codec round-trips. Both matter because the
spec is read live out of `node_modules/robosen-js` - if that package changes
under us, these are the tests that notice before the robot does.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import Report  # noqa: E402

from k1lib import protocol, spec  # noqa: E402

report = Report("protocol and spec")
check = report.check


def main():
    print("-- spec loads --")
    raw = spec.load()
    check("robot.json parsed", isinstance(raw, dict) and bool(raw))
    check("service uuid", spec.service_uuid().startswith("0000ffe0"), spec.service_uuid())
    check("characteristic uuid", spec.characteristic_uuid().startswith("0000ffe1"),
          spec.characteristic_uuid())

    print("\n-- joints are coherent --")
    joints = spec.joints()
    check("17 real joints", len(joints) == 17, str(len(joints)))
    indices = sorted(e["index"] for e in joints.values())
    check("indices are 0..16", indices == list(range(17)), str(indices))
    for name, entry in joints.items():
        lo, hi, mid = entry["min"], entry["max"], entry["neutral"]
        ok = lo <= mid <= hi and lo < hi
        check(f"{name}: {lo} <= {mid} <= {hi}", ok) if not ok else None
    check("all neutrals in range", all(e["min"] <= e["neutral"] <= e["max"]
                                      for e in joints.values()))

    speed = spec.speed()
    check("speed is index 24", speed["index"] == 24, str(speed["index"]))
    check("speed 1..100", (speed["min"], speed["max"]) == (1, 100))
    check("neutral speed is 30", speed["neutral"] == 30, str(speed["neutral"]))

    print("\n-- actions are complete --")
    actions = spec.actions()
    check("68 actions", len(actions) == 68, str(len(actions)))
    missing = [n for n, e in actions.items() if not e.get("duration_ms")]
    check("every action has a duration", not missing, str(missing[:5]))
    zero = [n for n, e in actions.items() if e["duration_ms"] <= 0]
    check("no zero-length actions", not zero, str(zero[:5]))
    bad_path = [n for n, e in actions.items() if not e["path"].startswith(("Action/", "ProAction/"))]
    check("paths are group-prefixed", not bad_path, str(bad_path[:3]))
    groups = {e["folder"] for e in actions.values()}
    check("only two groups", groups == {"Action", "ProAction"}, str(groups))

    print("\n-- every action path survives the codec --")
    bad = []
    for name, entry in actions.items():
        frame = protocol.packet(spec.code("action"), entry["path"])
        parsed = protocol.parse(frame)
        if not (parsed and parsed.valid and parsed.text() == entry["path"]):
            bad.append(name)
    check("all 68 action paths round-trip", not bad, str(bad[:5]))

    print("\n-- joint struct is 25 entries --")
    neutral = protocol.joint_struct()
    check("25 entries", len(neutral) == 25, str(len(neutral)))
    check("17 joints + 7 filler + speed",
          len(spec.joints()) == 17 and len(spec.fixed_joints()) == 8,
          f"{len(spec.fixed_joints())} fixed slots incl. speed")
    check("every real joint at its spec neutral",
          all(neutral[n] == e["neutral"] for n, e in spec.joints().items()))
    check("filler slots at spec values",
          all(neutral[k] == spec.fixed_joints()[k] for k in spec.fixed_joints()))
    check("speed entry is 30", neutral["speed"] == 30, str(neutral["speed"]))
    check("all joint values fit a byte",
          all(0 <= v <= 255 for v in neutral.values()),
          str([k for k, v in neutral.items() if not 0 <= v <= 255]))

    print("\n-- the known-good neutral frame --")
    # golden: ffff1be8813c6a76be92d4247b7b8173df74227e7a7d7d7d64647d7d1e96
    frame = protocol.packet(spec.code("jointMove"), protocol.joint_struct())
    check("neutral jointMove matches the oracle",
          frame.hex() == "ffff1be8813c6a76be92d4247b7b8173df74227e7a7d7d7d64647d7d1e96",
          frame.hex())

    print("\n-- a joint struct cannot be built empty --")
    for name in ("jointMove", "jointLock", "program"):
        try:
            protocol.packet(spec.code(name))
            check(f"empty {name} refused", False, "it built an all-zero pose")
        except ValueError as exc:
            check(f"empty {name} refused", "joint struct" in str(exc), str(exc)[:44])
    check("a request packet may be empty",
          protocol.packet(spec.code("state")).hex() == "ffff020f11")

    print("\n-- struct round-trips through the wire --")
    pose = {"head": 150, "leftThigh": 129, "speed": 40}
    blob = protocol.joint_struct(pose)
    check("struct is 25 raw bytes", len(blob) == 25, str(len(blob)))
    frame = protocol.packet(spec.code("jointMove"), blob)
    parsed = protocol.parse(frame)
    check("frame valid", parsed.valid)
    check("head survives", parsed.struct["head"] == 150, str(parsed.struct["head"]))
    check("leftThigh survives", parsed.struct["leftThigh"] == 129, str(parsed.struct["leftThigh"]))
    check("speed survives", parsed.struct["speed"] == 40, str(parsed.struct["speed"]))
    check("untouched joints stay at spec neutral",
          parsed.struct["rightThigh"] == spec.joints()["rightThigh"]["neutral"],
          str(parsed.struct["rightThigh"]))

    print("\n-- clamping --")
    head = spec.joints()["head"]
    check("above max clamps to max",
          protocol.clamp_pose({"head": 9999})["head"] == head["max"],
          str(protocol.clamp_pose({"head": 9999})["head"]))
    check("below min clamps to min",
          protocol.clamp_pose({"head": -9999})["head"] == head["min"],
          str(protocol.clamp_pose({"head": -9999})["head"]))
    check("in-range value untouched", protocol.clamp_pose({"head": 100})["head"] == 100)
    check("unknown joint dropped", "nonsense" not in protocol.clamp_pose({"nonsense": 5}))
    check("non-numeric dropped", "head" not in protocol.clamp_pose({"head": "left"}))
    check("bool is not a number", "head" not in protocol.clamp_pose({"head": True}))
    check("speed passes through", protocol.clamp_pose({"speed": 55}) == {"speed": 55})

    print("\n-- unknown names never reach the struct --")
    polluted = protocol.joint_struct({"nonsense": 5, "head": 100})
    check("joint_struct ignores unknown names", "nonsense" not in polluted, str(sorted(polluted)))
    check("still 25 entries", len(polluted) == 25, str(len(polluted)))

    print("\n-- corruption is always detected --")
    good = protocol.packet(spec.code("jointMove"), protocol.joint_struct())
    survivors = 0
    for i in range(len(good)):
        for bit in (0x01, 0x80):
            corrupt = bytearray(good)
            corrupt[i] ^= bit
            parsed = protocol.parse(bytes(corrupt))
            if parsed is None or not parsed.valid:
                survivors += 1
    total = len(good) * 2
    check(f"all {total} single-bit flips rejected", survivors == total, f"{survivors}/{total}")

    print("\n-- truncation is detected --")
    for cut in range(1, len(good)):
        parsed = protocol.parse(good[:cut])
        check(f"truncation to {cut} rejected", parsed is None or not parsed.valid)
        if report.failures:
            break

    return report.finish()


if __name__ == "__main__":
    sys.exit(main())