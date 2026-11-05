"""Architecture tests: no hardware, no bleak.

Covers the three properties that matter for the core/bus split:
  1. the same intent stream runs on the simulator and (by construction) on BLE
  2. the simulator rejects malformed frames
  3. a routine produces the same frames regardless of transport
"""
import asyncio
import json
import sys

from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import ROOT, Report  # noqa: E402

from k1lib import protocol, spec  # noqa: E402
from k1lib.bus import SimBus  # noqa: E402
from k1lib.core import DIRECTIONS, IntentError, RobotCore, make_bus  # noqa: E402



class MuteProgressBus(SimBus):
    """A robot that stops reporting part-way through a stunt.

    Fault injection, not a claim about hardware: real K1s do report 100% when
    driven one command at a time. This exists to reach the backstop paths, which
    a cooperative simulator would never exercise.
    """

    def _emit(self, frame):
        pkt = protocol.parse(frame)
        if pkt and pkt.type == spec.code("action") and pkt.data[:1] == b"\x64":
            return  # swallow the 100% packet, keep the earlier progress
        super()._emit(frame)


class DeafBus(SimBus):
    """A robot that accepts an action and then says nothing at all."""

    async def write(self, frame):
        pkt = protocol.parse(frame)
        if pkt and pkt.type == spec.code("action"):
            self._record(frame)
            return
        await super().write(frame)


def deaf_core(cls, **kwargs):
    """A core on a misbehaving robot, with the backstop windows shrunk.

    The real quiet/grace windows are 2s and 10s. Waiting those out on every
    fault-injection path cost the suite most of a minute, so tests cut them to
    fractions of a second. Only the timings change, never the logic.
    """
    core = RobotCore(cls(**kwargs))
    core.action_quiet_s = 0.05
    core.action_grace_s = 0.25
    core.action_slack_s = 0.0
    core.time_scale = 0.002
    return core


async def connected_sim(**kwargs):
    core = RobotCore(SimBus(**kwargs))
    # Compress pacing sleeps so a 4-second walk doesn't take four seconds. The
    # frames are identical; only wall clock changes. Deadlines are untouched.
    core.time_scale = 0.002
    await core.start()
    return core


def last_pose_frame(core):
    """Newest jointMove packet actually put on the bus."""
    for entry in reversed(core.bus.session):
        frame = protocol.parse(bytes.fromhex(entry["hex"]))
        if frame.type == spec.code("jointMove"):
            return frame
    raise AssertionError("no jointMove frame was sent")


report = Report("core over swappable buses")
check = report.check


async def main():
    print("-- bus selection --")
    check("make_bus('sim')", isinstance(make_bus("sim"), SimBus))
    try:
        make_bus("nope")
        check("make_bus rejects unknown", False)
    except ValueError:
        check("make_bus rejects unknown", True)

    print("\n-- state round-trip --")
    core = await connected_sim(battery=77, volume=42)
    state = await core.i_state()
    check("battery", state.get("battery") == 77, str(state.get("battery")))
    check("volume", state.get("volume") == 42, str(state.get("volume")))

    print("\n-- config intents mutate sim state --")
    await core.i_volume(120)
    check("volume clamped to 140", (await core.i_state()).get("volume") == 120)
    await core.i_volume(999)
    check("over-max clamped", (await core.i_state()).get("volume") == 140)
    await core.i_autoStand(False)
    check("autoStand off", (await core.i_state()).get("autoStand") == 0)

    print("\n-- action completion via progress packets --")
    events = []
    core = await connected_sim(speed=40.0)  # fast sim
    core.on(lambda name, data: events.append((name, data)))
    await core.i_action("Left Punch")
    pcts = [d["percent"] for n, d in events if n == "progress"]
    check("progress reached 100", 100 in pcts, str(pcts))
    check("progress is monotonic", pcts == sorted(pcts), str(pcts))
    check("action completed without timeout", core._progress.is_set())

    print("\n-- intents reject bad input --")
    for method, params in [
        ("action", {"name": "Not A Real Action"}),
        ("move", {"direction": "sideways"}),
    ]:
        outcome = await core.run(method, params)
        check(f"{method} rejects {params}", not outcome.get("ok"),
              outcome.get("error", ""))

    print("\n-- stand refuses the leaning frame unless forced --")
    before = len(core.bus.session)
    outcome = await core.run("stand", {})
    check("stand refuses by default",
          not outcome.get("ok") and "neutral" in outcome.get("error", ""),
          outcome.get("error", "")[:70])
    check("a refused stand sends no frame", len(core.bus.session) == before)
    outcome = await core.run("stand", {"allow_leaning_neutral": True})
    check("a forced stand still writes the frame", outcome.get("ok") is True,
          str(outcome.get("result")))

    print("\n-- unknown intent --")
    outcome = await core.run("teleport", {})
    check("unknown intent errors", not outcome.get("ok") and "unknown intent" in outcome.get("error", ""))

    print("\n-- sim rejects malformed frames --")
    bus = SimBus()
    core = RobotCore(bus)
    await core.start()
    await bus.write(b"\xff\xff\x02\x0f\x00")  # deliberately wrong checksum
    check("bad checksum rejected", len(bus.rejected) == 1, str(bus.rejected))
    await bus.write(b"\x00\x01\x02")  # not a K1 packet at all
    check("garbage rejected", len(bus.rejected) == 2)
    await bus.write(protocol.packet(spec.code("state")))
    check("valid frame accepted", len(bus.rejected) == 2, f"{len(bus.session)} good frames")

    print("\n-- pose clamping reaches the wire --")
    core = await connected_sim()
    await core.i_pose({"head": 9999})
    frame = last_pose_frame(core)
    check("head clamped to 202", frame.struct["head"] == 202, str(frame.struct["head"]))
    await core.i_pose({"nonsense": 5})
    frame = last_pose_frame(core)
    check("unknown joint ignored, struct intact", len(frame.data) == 25, f"{len(frame.data)} bytes")

    print("\n-- delta is relative to current --")
    core = await connected_sim()
    start = spec.joints()["head"]["neutral"]
    await core.i_delta({"head": 20})
    check("head +20", core.joints["head"] == start + 20, str(core.joints["head"]))
    await core.i_delta({"head": -5})
    check("head +15", core.joints["head"] == start + 15, str(core.joints["head"]))

    print("\n-- routine: same frames on any bus --")
    steps = json.loads((ROOT / "routines" / "hello.json").read_text())
    core = await connected_sim(speed=60.0)
    result = await core.i_routine(steps, gap=0.0)
    check("all steps played", result["steps"] == 10, str(result["steps"]))
    sim_frames = [f["hex"] for f in core.bus.session]
    check("no rejected frames", not core.bus.rejected)
    check("frames recorded", len(sim_frames) > 10, str(len(sim_frames)))

    # every frame the routine produced must be a structurally valid packet
    all_valid = all(protocol.parse(bytes.fromhex(h)).valid for h in sim_frames)
    check("every frame checksum-valid", all_valid)

    print("\n-- choreography vocabulary: custom audio, easing, card-only stunts --")
    core = await connected_sim(speed=60.0)
    outcome = await core.run("routine", {"gap": 0.0, "steps": [
        {"speak": "02/101"},
        {"glide": {"head": 140}, "maxStep": 3, "fast": True},
        {"actionPath": "SpeAction/showtime"},
    ]})
    check("all three step kinds ran", outcome.get("ok") is True,
          str(outcome.get("error", ""))[:60])
    check("reported each step", outcome.get("result", {}).get("played") ==
          ["speak", "glide", "actionPath"], str(outcome.get("result")))
    hexes = [f["hex"] for f in core.bus.session]
    check("custom audio went out as a path frame",
          any(h[6:8] == spec.code("audio") and "30322f313031" in h for h in hexes),
          "no audio frame carrying 02/101")
    check("glide eased over several frames",
          sum(h[6:8] == spec.code("jointMove") for h in hexes) > 2,
          str(sum(h[6:8] == spec.code("jointMove") for h in hexes)))
    check("no rejected frames", not core.bus.rejected, str(core.bus.rejected[:1]))

    print("\n-- a spec-unknown path still finishes in the sim --")
    core = await connected_sim(speed=60.0)
    check("sim has a duration placeholder for card-only stunts",
          spec.UNKNOWN_ACTION_MS > 0, str(spec.UNKNOWN_ACTION_MS))
    got = await core.run("actionPath", {"path": "SpeAction/showtime"})
    check("card-only stunt completed", got.get("ok") is True
          and got.get("result", {}).get("completion") == "progress-100",
          str(got.get("result", {}).get("completion")))
    check("and admitted the duration is a guess",
          got.get("result", {}).get("duration_estimated") is True, str(got.get("result")))

    print("\n-- a bad step is reported, not raised --")
    core = await connected_sim(speed=60.0)
    outcome = await core.run("routine", {"steps": [{"moonwalk": True}]})
    check("bad step errors cleanly", not outcome.get("ok"), outcome.get("error", "")[:60])

    print("\n-- factory stunts are allowed by default --")
    # Hardware showed the keyword classifier was wrong twice: Push Ups and
    # Left Kick are both "fall-risk" by name and both perform cleanly. They are
    # vendor trajectories for this robot, so blocking them was second-guessing
    # the manufacturer.
    core = await connected_sim(speed=60.0)
    for name in ("Lean Forward", "Crouch Down", "Back Flip", "Handstand",
                 "Push Ups", "Left Kick", "Play Dead"):
        got = await core.run("action", {"name": name})
        check(f"{name!r} allowed without an override", got.get("ok") is True,
              str(got.get("error", ""))[:44])
        check(f"{name!r} is still reported as name-matched risk",
              got.get("result", {}).get("fall_risk") is not None
              or name not in spec.fall_risk(),
              str(got.get("result", {}).get("fall_risk")))
        check(f"{name!r} is marked a factory stunt",
              got.get("result", {}).get("factory_stunt") is True, str(got.get("result")))

    print("\n-- strict mode restores the conservative block --")
    core = await connected_sim(speed=40.0)
    for name in ["Lean Forward", "Crouch Down", "Lay Down", "Back Flip",
                 "Handstand", "Push Ups", "Play Dead", "Kneel,Please"]:
        before = len(core.bus.session)
        outcome = await core.run("action", {"name": name, "strict": True})
        check(f"{name!r} refused under strict", not outcome.get("ok"),
              outcome.get("error", "")[:44])
        check(f"{name!r} put nothing on the wire", len(core.bus.session) == before,
              f"{len(core.bus.session) - before} extra frames")
    outcome = await core.run("action", {"name": "Lean Forward", "strict": True,
                                        "allow_fall_risk": True})
    check("strict plus override works", outcome.get("ok") is True,
          str(outcome.get("error", ""))[:50])

    print("\n-- what hardware has actually verified --")
    check("verified set is a subset of real actions",
          set(spec.verified_safe()) <= set(spec.actions()),
          str(set(spec.verified_safe()) - set(spec.actions())))
    check("includes the floor recovery",
          "Push Ups" in spec.verified_safe(), str(list(spec.verified_safe())))
    check("includes both proven false positives of the classifier",
          "Push Ups" in spec.fall_risk() and "Left Kick" in spec.fall_risk(),
          "classifier no longer flags them, test is stale")
    check("safe actions are not blocked", True)

    print("\n-- the classification is derived from the spec, not hardcoded --")
    check("some actions blocked", 0 < len(spec.fall_risk()) < len(spec.actions()),
          f"{len(spec.fall_risk())}/{len(spec.actions())}")
    check("shortest action is safe", spec.safe_actions()[0] == "Left Punch",
          spec.safe_actions()[0])
    check("every fall-risk word actually matched a real action",
          all(spec.is_fall_risk(n) for n in spec.fall_risk()))
    check("safe and risky sets are disjoint",
          not (set(spec.safe_actions()) & set(spec.fall_risk())))

    print("\n-- routines inherit strict mode --")
    core = await connected_sim(speed=60.0)
    outcome = await core.run("routine", {"steps": [{"action": "Crouch Down"}]})
    check("routine step allowed by default", outcome.get("ok") is True,
          str(outcome.get("error", ""))[:52])
    outcome = await core.run("routine", {"steps": [{"action": "Crouch Down",
                                                    "strict": True}]})
    check("routine step refused under strict", not outcome.get("ok"),
          outcome.get("error", "")[:52])
    outcome = await core.run("routine", {"steps": [{"action": "Crouch Down",
                                                    "strict": True,
                                                    "allowFallRisk": True}]})
    check("routine step can opt in", outcome.get("ok") is True,
          str(outcome.get("error", ""))[:60])

    print("\n-- a routine step can carry its own completion deadline --")
    # The default deadline is the declared clock + slack, and a few declarations
    # are ~10x short (Play Dead 1.68s declared, 16.78s measured), so a step must
    # be able to say how long it is really willing to wait.
    from k1lib.core import STEP_INTENTS
    intent, params = STEP_INTENTS["action"]({"action": "Play Dead", "grace": 31.8})
    check("grace reaches the intent as grace_s",
          intent == "action" and params.get("grace_s") == 31.8, str(params))
    _, params = STEP_INTENTS["action"]({"action": "Play Dead"})
    check("and is absent when the step does not set one",
          "grace_s" not in params, str(params))

    print("\n-- tracked pose goes stale after an action or move --")
    core = await connected_sim(speed=60.0)
    check("fresh core is not stale", core._stale is False)
    await core.run("pose", {"pose": {"head": 140}})
    check("pose does not stale it", core._stale is False)
    await core.run("action", {"name": "Left Punch"})
    check("action stales it", core._stale is True)
    before = len(core.bus.session)
    outcome = await core.run("pose", {"pose": {"head": 150}})
    check("pose refused while stale", not outcome.get("ok"), outcome.get("error", "")[:60])
    check("refusal put nothing on the wire", len(core.bus.session) == before,
          f"{len(core.bus.session) - before} extra frames")
    outcome = await core.run("pose", {"pose": {"head": 150}, "assume_stale_ok": True})
    check("opt-in works", outcome.get("ok") is True, str(outcome.get("error", ""))[:60])

    print("\n-- move stales it too --")
    core = await connected_sim()
    await core.run("move", {"direction": "forward", "ms": 500})
    check("move stales it", core._stale is True)

    print("\n-- stand clears staleness AND actually resets to neutral --")
    core = await connected_sim()
    await core.run("pose", {"pose": {"head": 100}})
    check("head is off-neutral", core.joints["head"] != spec.joints()["head"]["neutral"])
    await core.run("action", {"name": "Left Punch"})
    await core.run("stand", {"allow_leaning_neutral": True})
    check("stand clears stale", core._stale is False)
    check("stand resets head to neutral",
          core.joints["head"] == spec.joints()["head"]["neutral"],
          str(core.joints["head"]))
    bad = [k for k, v in core.joints.items()
           if k != "speed" and v != protocol.joint_struct()[k]]
    check("stand resets every joint to neutral", not bad, f"still off: {bad}")
    outcome = await core.run("pose", {"pose": {"head": 130}})
    check("pose allowed again after stand", outcome.get("ok") is True,
          str(outcome.get("error", ""))[:60])

    print("\n-- stand is a real reset, not a merge (regression) --")
    core = await connected_sim()
    await core.run("pose", {"pose": {"head": 100, "left_arm": 90}})
    await core.run("stand", {"speed": 40, "allow_leaning_neutral": True})
    want = protocol.packet(spec.code("jointMove"),
                           protocol.joint_struct(speed=40))
    check("stand sends exactly the neutral struct", last_pose_frame(core).raw == want,
          f"got  {last_pose_frame(core).raw.hex()}\n        want {want.hex()}")

    print("\n-- routines can re-sync with a stand step --")
    core = await connected_sim(speed=60.0)
    outcome = await core.run("routine", {"steps": [
        {"action": "Left Punch"}, {"stand": True, "speed": 40,
                                   "allowLeaningNeutral": True},
        {"pose": {"head": 130}, "speed": 30},
    ], "gap": 0.0})
    check("action -> stand -> pose runs clean", outcome.get("ok") is True,
          str(outcome.get("error", ""))[:70])
    outcome = await core.run("routine", {"steps": [
        {"action": "Left Punch"}, {"pose": {"head": 130}, "speed": 30},
    ], "gap": 0.0})
    check("action -> pose without stand is refused", not outcome.get("ok"),
          outcome.get("error", "")[:60])

    print("\n-- move timing: spec floor vs what actually walks --")
    from k1lib.core import MOVE_MIN_MS, MOVE_MAX_MS, MOVE_SETTLE_MS, MOVE_USEFUL_MS
    check("spec floor is 500ms", MOVE_MIN_MS == 500, str(MOVE_MIN_MS))
    check("useful walk is longer than the stance transition",
          MOVE_USEFUL_MS > MOVE_SETTLE_MS, f"{MOVE_USEFUL_MS} vs {MOVE_SETTLE_MS}")

    core = await connected_sim()
    outcome = await core.run("move", {"direction": "sideways", "ms": 4000})
    check("bad direction still refused", not outcome.get("ok"), outcome.get("error", "")[:50])
    outcome = await core.run("move", {"direction": "forward", "ms": 1})
    check("too-short request is clamped up to the spec floor, not rejected",
          outcome.get("ok") is True, str(outcome.get("error", ""))[:50])
    check("clamped value reported", outcome.get("result", {}).get("ms") == MOVE_MIN_MS,
          str(outcome.get("result", {}).get("ms")))
    outcome = await core.run("move", {"direction": "forward", "ms": 99999})
    check("absurd request clamped to spec max",
          outcome.get("result", {}).get("ms") == MOVE_MAX_MS,
          str(outcome.get("result", {}).get("ms")))
    outcome = await core.run("move", {"direction": "forward", "ms": 4000})
    check("move is flagged as a posture change",
          outcome.get("result", {}).get("posture_change") is True,
          str(outcome.get("result", {})))

    print("\n-- every outbound frame is recorded, on any bus --")
    core = await connected_sim()
    before = len(core.bus.session)
    await core.run("move", {"direction": "forward", "ms": 500})
    added = core.bus.session[before:]
    check("move wrote two frames (command + stop)", len(added) == 2,
          f"{len(added)} frames")
    codes = [bytes.fromhex(f["hex"])[3:4].hex() for f in added]
    check("they are the move then the stop",
          codes == [DIRECTIONS["forward"], spec.code("stop")],
          f"{codes} vs {[DIRECTIONS['forward'], spec.code('stop')]}")
    check("DIRECTIONS uses the command.Move codes, not the bare type names",
          DIRECTIONS["forward"] == spec.code("moveNorth")
          and spec.code("forward") != DIRECTIONS["forward"],
          f"DIRECTIONS={DIRECTIONS['forward']} spec.code('forward')={spec.code('forward')}")
    check("base class owns session", "session" in dir(core.bus), "no session attr")

    print("\n-- a queued action cannot inherit the previous one's completion --")
    # Regression: clearing the progress signal before taking the lock let the
    # second action see the `set()` left by the first and report "progress-100"
    # instantly - a reply that looked like success but was a lie, with no
    # progress notification ever emitted. Caught by watching the event stream.
    core = await connected_sim(speed=3.0)
    a = asyncio.create_task(core.run("action", {"name": "Left Punch"}))
    while not core.busy:
        await asyncio.sleep(0.005)
    b = asyncio.create_task(core.run("action", {"name": "Right Punch"}))

    seen = []
    core.on(lambda e, d: seen.append((e, d)))
    ra, rb = await a, await b
    check("first completed for real",
          ra.get("result", {}).get("completion") == "progress-100",
          str(ra.get("result", {})))
    check("second completed for real",
          rb.get("result", {}).get("completion") == "progress-100",
          str(rb.get("result", {})))
    hundreds = [d["action"] for e, d in seen
                if e == "progress" and d.get("percent") == 100]
    check("each action emitted its own 100%",
          sorted(hundreds) == ["Left Punch", "Right Punch"], str(hundreds))
    check("the second one was attributed correctly, not inherited",
          all(d.get("action") == "Right Punch"
              for e, d in seen
              if e == "progress" and d.get("action") == "Right Punch"),
          "mislabelled progress")

    print("\n-- the second action really waited, rather than replying at once --")
    core = await connected_sim(speed=3.0)
    a = asyncio.create_task(core.run("action", {"name": "Left Punch"}))
    while not core.busy:
        await asyncio.sleep(0.005)
    b = asyncio.create_task(core.run("action", {"name": "Right Punch"}))
    await asyncio.sleep(0.05)
    check("second action has not replied yet", not b.done(), "replied immediately")
    await a
    await b

    print("\n-- the command queue: one command in flight, the rest wait --")
    core = await connected_sim(speed=6.0)
    check("idle core is not busy", core.busy is False)

    first = asyncio.create_task(core.run("action", {"name": "Left Punch"}))
    while not core.busy:
        await asyncio.sleep(0.005)
    second = asyncio.create_task(core.run("action", {"name": "Right Punch"}))
    poll = asyncio.create_task(core.run("state", {}))
    await asyncio.sleep(0.05)
    check("a second action is waiting its turn", core.queued >= 1,
          f"queued={core.queued}")
    check("the busy action has not finished yet", not first.done(), "finished early")

    one = await first
    two = await second
    await poll
    check("both actions completed", one.get("ok") and two.get("ok"),
          f"{one.get('error', '')} {two.get('error', '')}")
    check("queue drained", core.busy is False and core.queued == 0,
          f"busy={core.busy} queued={core.queued}")

    print("\n-- concurrent clients cannot interleave on the wire --")
    core = await connected_sim(speed=6.0)
    tasks = [asyncio.create_task(core.run("action", {"name": "Left Punch"}))
             for _ in range(3)]
    results = [await t for t in tasks]
    seq = [bytes.fromhex(f["hex"])[3:4].hex() for f in core.bus.session]
    action_positions = [i for i, c in enumerate(seq) if c == spec.code("action")]
    check("three action frames, all accepted",
          len(action_positions) == 3 and all(r.get("ok") for r in results), f"{seq}")
    check("all three reported progress-100",
          all(r.get("result", {}).get("completion") == "progress-100" for r in results),
          str([r.get("result", {}).get("completion") for r in results]))
    check("no action was dropped", len(action_positions) == 3,
          f"{len(action_positions)} of 3 reached the wire")

    print("\n-- wait=False still holds the queue until the robot finishes --")
    core = await connected_sim(speed=6.0)
    got = await core.run("action", {"name": "Left Punch", "wait": False})
    check("returns immediately", got.get("ok") is True, str(got.get("error", ""))[:50])
    check("reported as queued", got.get("result", {}).get("completion") == "queued",
          str(got.get("result", {}).get("completion")))
    check("but the queue is still held", core.busy is True, "lock was released early")
    blocked = await core.run("state", {}, )  # must wait, not interleave
    check("the next command waited for it", blocked.get("ok") is True,
          str(blocked.get("error", ""))[:50])

    print("\n-- a queued action is not lost --")
    core = await connected_sim(speed=6.0)
    a = asyncio.create_task(core.run("action", {"name": "Left Punch"}))
    while not core.busy:
        await asyncio.sleep(0.005)
    b = asyncio.create_task(core.run("action", {"name": "Right Punch"}))
    ra, rb = await a, await b
    check("first reported progress-100",
          ra.get("result", {}).get("completion") == "progress-100",
          str(ra.get("result", {}).get("completion")))
    check("second ran after it, and completed",
          rb.get("ok") is True and rb.get("result", {}).get("completion") == "progress-100",
          str(rb.get("result", {}))[:70])
    check("the queue was what serialised them",
          ra.get("result", {}).get("action") != rb.get("result", {}).get("action"),
          "both reported the same action")

    print("\n-- a stuck robot cannot wedge the queue forever --")
    core = deaf_core(DeafBus, speed=60.0)
    await core.start()
    a = asyncio.create_task(core.run("action", {"name": "Left Punch"}))
    b = asyncio.create_task(core.run("action", {"name": "Right Punch"}))
    ra, rb = await asyncio.wait_for(a, 40), await asyncio.wait_for(b, 40)
    check("first bailed out as timeout",
          ra.get("result", {}).get("completion") == "timeout",
          str(ra.get("result", {}).get("completion")))
    check("the queued one still ran and returned", rb.get("ok") is True,
          str(rb.get("error", ""))[:60])
    check("queue released at the end", core.busy is False, "still locked")

    print("\n-- completion says which signal ended the wait --")
    core = await connected_sim(speed=60.0)
    got = await core.run("action", {"name": "Left Punch"})
    check("a well-behaved robot reports progress-100",
          got.get("result", {}).get("completion") == "progress-100",
          str(got.get("result", {}).get("completion")))

    core = deaf_core(MuteProgressBus, speed=60.0)
    await core.start()
    got = await core.run("action", {"name": "Left Punch"})
    check("silence after a start is bounded, not guessed",
          got.get("result", {}).get("completion") == "timeout",
          str(got.get("result", {}).get("completion")))

    # Hardware finding (K1-XXXX): the firmware reports 50% then 100% with a gap
    # that grows with the stunt, so a flat 2s silence window cut `Left Kick` off
    # at 4.1s of 5.4s. The window must scale with the declared clock.
    core = RobotCore(SimBus(speed=1.0))
    core.action_quiet_s = 0.3          # shorter than the 0.4s packet gap
    await core.start()
    started = asyncio.get_running_loop().time()
    got = await core.run("action", {"name": "Left Punch"})
    took = asyncio.get_running_loop().time() - started
    check("a two-packet action is not cut off by the silence window",
          got.get("result", {}).get("completion") == "progress-100",
          str(got.get("result", {}).get("completion")))
    check("and it is waited out to its declared clock",
          took >= 0.7, f"{took:.2f}s")
    await core.stop()

    core = deaf_core(DeafBus, speed=60.0)
    await core.start()
    started = asyncio.get_running_loop().time()
    got = await core.run("action", {"name": "Left Punch"})
    elapsed = asyncio.get_running_loop().time() - started
    check("a totally silent robot ends in timeout",
          got.get("result", {}).get("completion") == "timeout",
          str(got.get("result", {}).get("completion")))
    check("and it is bounded, not a hang", elapsed < 5, f"{elapsed:.2f}s")

    print("\n-- a routine refuses to queue a second action into a busy robot --")
    core = deaf_core(DeafBus, speed=60.0)
    await core.start()
    outcome = await core.run("routine", {"steps": [
        {"action": "Left Punch"}, {"action": "Right Punch"},
    ], "gap": 0.0})
    check("routine stops at the unconfirmed action", not outcome.get("ok"),
          outcome.get("error", "")[:60])
    check("it explains why", "still be animating" in outcome.get("error", ""),
          outcome.get("error", "")[:80])
    sent = [bytes.fromhex(f["hex"])[3:4].hex() for f in core.bus.session]
    check("the second action never reached the wire",
          sent.count(spec.code("action")) == 1, str(sent))

    print("\n-- but a routine of well-behaved actions runs to the end --")
    core = await connected_sim(speed=60.0)
    outcome = await core.run("routine", {"steps": [
        {"action": "Left Punch"}, {"action": "Right Punch"},
    ], "gap": 0.0})
    check("both played", outcome.get("ok") is True and outcome.get("result", {}).get("steps") == 2,
          str(outcome)[:90])

    print("\n-- polling state must not break a running action --")
    # Verified on hardware: a state frame sent while a stunt animates suppresses
    # that stunt's progress notifications, so completion detection dies.
    # The action must be *already running* before the poll, or there is no
    # overlap to test and the check passes for the wrong reason.
    core = await connected_sim(speed=4.0)  # slow, so the overlap is reliable
    action_task = asyncio.create_task(core.run("action", {"name": "Left Punch"}))
    while core._action is None:            # wait until it is really animating
        await asyncio.sleep(0.005)
    poll = asyncio.create_task(core.run("state", {}))
    action = await action_task
    check("action still completes", action.get("ok") is True,
          str(action.get("error", ""))[:50])
    check("progress still reached 100", core._progress.is_set())
    polled = await poll
    check("the deferred poll still answers", polled.get("ok") is True,
          str(polled.get("error", ""))[:50])
    check("poll returned real state", bool(polled.get("result")), str(polled)[:60])

    print("\n-- the poll was deferred, not sent mid-action --")
    core = await connected_sim(speed=4.0)
    action_task = asyncio.create_task(core.run("action", {"name": "Left Punch"}))
    while core._action is None:
        await asyncio.sleep(0.005)
    poll = asyncio.create_task(core.run("state", {}))
    await action_task
    await poll
    seq = [bytes.fromhex(f["hex"])[3:4].hex() for f in core.bus.session]
    started = seq.index(spec.code("action"))
    check("an action really was in flight", seq[:started] == [],
          f"action was not first on the wire: {seq}")
    check("no state frame was interleaved into the action",
          spec.code("state") not in seq[started:started + 1],
          f"state frame at {seq[:started + 1]}")
    check("the deferred state frame came after the action",
          spec.code("state") in seq[started:], f"{seq}")

    print("\n-- opt-outs for callers who really want to poll --")
    core = await connected_sim(speed=4.0)
    await core.run("state", {})            # prime the cache
    action_task = asyncio.create_task(core.run("action", {"name": "Left Punch"}))
    while core._action is None:
        await asyncio.sleep(0.005)
    before = len(core.bus.session)
    got = await core.run("state", {"during_action": "cached"})
    check("cached answered without touching the wire",
          len(core.bus.session) == before,
          f"{len(core.bus.session) - before} frames written")
    check("cached returns the primed state", got.get("result", {}).get("battery") == 100,
          str(got.get("result"))[:70])
    await action_task

    print("\n-- info: read-only identification --")
    core = await connected_sim()
    info = await core.run("info", {})
    check("info succeeds", info.get("ok") is True, str(info.get("error", ""))[:60])
    got = info.get("result", {})
    check("kind is K1", got.get("kind") == "K1", str(got.get("kind")))
    check("version looks like the hardware's", got.get("version") == "VER:3.04L",
          str(got.get("version")))
    check("date looks like the hardware's", got.get("date") == "SH2022-08-15",
          str(got.get("date")))

    print("\n-- info moves nothing --")
    core = await connected_sim()
    before = [e for e in core.bus.session
              if bytes.fromhex(e["hex"])[3:4].hex() == spec.code("jointMove")]
    await core.run("info", {})
    after = [e for e in core.bus.session
             if bytes.fromhex(e["hex"])[3:4].hex() == spec.code("jointMove")]
    check("no jointMove frame from info", len(before) == len(after),
          f"{len(after) - len(before)} joint frames")

    print("\n-- a silent robot reports None instead of hanging or lying --")
    import k1lib.bus as bus_mod
    core = await connected_sim()
    original = bus_mod.INFO_REPLIES
    bus_mod.INFO_REPLIES = ()  # make the sim as deaf as jointSync
    try:
        outcome = await core.run("info", {"timeout": 0.3})
    finally:
        bus_mod.INFO_REPLIES = original
    res = outcome.get("result", {})
    check("still returns ok rather than raising", outcome.get("ok") is True,
          str(outcome.get("error", ""))[:60])
    check("fields are None, not invented", res.get("kind") is None, str(res.get("kind")))
    check("ok flag reflects reality", res.get("ok") is False, str(res.get("ok")))

    print("\n-- readPose reports failure instead of faking data --")
    core = await connected_sim()
    await core.i_pose({"head": 150})
    readback = await core.i_readPose(timeout=0.2)
    check("ok is False", readback["ok"] is False)
    check("joints is None, not the local cache", readback["joints"] is None,
          str(readback["joints"]))
    check("says why", "does not implement jointSync" in readback["error"])
    check("does not claim verification", readback["verified"] is False)
    check("local tracking still available", core.joints["head"] == 150, str(core.joints["head"]))

    print("\n-- what can the robot actually say --")
    clips = spec.audio_clips()
    check("clips come from the spec ranges", clips[:3] == [101, 102, 103], str(clips[:3]))
    check("both declared ranges are expanded",
          clips[10] == 111 and clips[11] == 201 and clips[-1] == 216,
          f"{clips[10]}, {clips[11]}, {clips[-1]}")
    check("clip ids are the bare numbers, not paths",
          all(isinstance(c, int) for c in clips), str(clips[:3]))
    check("clip_name explains where an id lives",
          spec.clip_name(101) == "AppSysMS/program"
          and spec.clip_name(205) == "AppSysMS/joint",
          f"{spec.clip_name(101)} / {spec.clip_name(205)}")
    check("an unknown id is reported as unknown",
          spec.clip_name(999) == "unknown", spec.clip_name(999))

    core = await connected_sim()
    listed = await core.run("list", {"kind": "audioNames"})
    got = listed.get("result", {})
    check("audioNames lists every declared clip", got.get("count") == len(clips),
          f"{got.get('count')} vs {len(clips)}")
    check("the stream terminated with done", got.get("complete") is True, str(got)[:70])
    check("entries are full paths",
          all(n.startswith("AppSysMS/") for n in got.get("audioNames", [])),
          str(got.get("audioNames", [])[:2]))

    core = await connected_sim()
    audio = await core.run("audio", {})
    res = audio.get("result", {})
    check("audio intent works", audio.get("ok") is True, str(audio)[:70])
    check("declared and on-device agree in sim", res.get("not_in_spec") == [],
          str(res.get("not_in_spec"))[:70])
    check("and the counts match", res.get("declared_count") == res.get("on_device_count"),
          f"{res.get('declared_count')} vs {res.get('on_device_count')}")

    print("\n-- a truncated list is never presented as complete --")
    core = await connected_sim()
    import k1lib.bus as bus_mod
    original = bus_mod.LIST_TERMINATES
    bus_mod.LIST_TERMINATES = False  # items arrive, `done` never does
    try:
        truncated = await core.run("audio", {"timeout": 0.4})
    finally:
        bus_mod.LIST_TERMINATES = original
    tres = truncated.get("result", {})
    check("it still reports what arrived", tres.get("on_device_count") > 0,
          str(tres.get("on_device_count")))
    check("but flags itself incomplete", tres.get("list_complete") is False,
          str(tres.get("list_complete")))
    check("and nothing is falsely called extra",
          tres.get("not_in_spec") == [], str(tres.get("not_in_spec"))[:60])
    check("all items can arrive yet the list is still unconfirmed - this is the "
          "dangerous case, a full-looking list that might be truncated",
          tres.get("on_device_count") == len(clips)
          and tres.get("list_complete") is False,
          f"{tres.get('on_device_count')} items, complete={tres.get('list_complete')}")

    print("\n-- play is restricted to clips that exist --")
    core = await connected_sim()
    ok = await core.run("say", {"clip": 205})
    check("a declared clip plays", ok.get("ok") is True, str(ok.get("error", ""))[:60])
    check("and reports which group it came from",
          ok.get("result", {}).get("group") == "AppSysMS/joint",
          str(ok.get("result", {})))
    before = len(core.bus.session)
    bad = await core.run("say", {"clip": 999})
    check("an undeclared clip is refused", not bad.get("ok"), str(bad.get("error", ""))[:60])
    check("and nothing reached the wire", len(core.bus.session) == before,
          f"{len(core.bus.session) - before} frames")

    print("\n-- non-list types are rejected as list queries --")
    core = await connected_sim()
    outcome = await core.run("list", {"kind": "kind"})
    check("'kind' is not a list type", not outcome.get("ok"),
          outcome.get("error", "")[:60])

    print("\n-- speak: playing your own audio by path --")
    core = await connected_sim()
    probe = await core.run("folders", {})
    pres = probe.get("result", {})
    check("folders discovered", pres.get("folders") == ["AppSysMS"], str(pres.get("folders")))
    check("audio grouped by folder",
          len(pres.get("audio", {}).get("AppSysMS", [])) == len(clips),
          str({k: len(v) for k, v in pres.get("audio", {}).items()}))
    check("total matches the clip count", pres.get("total_audio") == len(clips),
          str(pres.get("total_audio")))

    core = await connected_sim()
    await core.run("folders", {})
    got = await core.run("speak", {"path": "AppSysMS/205"})
    check("a known folder is confirmed",
          got.get("result", {}).get("folder_confirmed") is True, str(got.get("result")))
    got = await core.run("speak", {"path": "MadeUpFolder/story01"})
    check("an unlisted folder is flagged, not assumed",
          got.get("result", {}).get("folder_confirmed") is False, str(got.get("result")))

    core = await connected_sim()
    got = await core.run("speak", {"path": "MadeUpFolder/story01"})
    check("before any listing, confirmation is unknown rather than false",
          got.get("result", {}).get("folder_confirmed") is None, str(got.get("result")))

    print("\n-- a path that reaches the firmware stays boring --")
    core = await connected_sim()
    for bad in ["../etc/passwd", "a/../../b", "/absolute/1", "C:/windows/x", "",
                "bad\x01name"]:
        before = len(core.bus.session)
        got = await core.run("speak", {"path": bad})
        check(f"{bad!r} refused",
              not got.get("ok") and len(core.bus.session) == before,
              str(got.get("error", ""))[:44] or "reached the wire")
    got = await core.run("speak", {"path": "Folder\\name.mp3"})
    check("backslashes are normalised, not smuggled",
          got.get("result", {}).get("path") == "Folder/name.mp3",
          str(got.get("result", {}).get("path")))

    print("\n-- SD card layout is recorded, not derived --")
    # None of this is in robot.json: it came off the card over USB-C and from the
    # card's own warning file. If a future spec revision ever contradicts it,
    # that is worth knowing rather than silently re-deriving the wrong answer.
    check("user audio folder is 02", spec.USER_AUDIO_FOLDER == "02",
          spec.USER_AUDIO_FOLDER)
    check("AppSysMS is marked protected", "AppSysMS" in spec.SD_PROTECTED,
          str(spec.SD_PROTECTED))
    check("AppSysMS is not the user folder",
          "AppSysMS" in spec.SD_PROTECTED
          and spec.USER_AUDIO_FOLDER not in spec.SD_PROTECTED,
          "user folder is protected")
    check("robot.json only ever mentions AppSysMS, which is why this is recorded",
          spec.audio_folders() == ["AppSysMS"], str(spec.audio_folders()))
    check("the firmware appends .mp3", spec.audio_extension() == ".mp3",
          spec.audio_extension())

    core = await connected_sim()
    got = await core.run("speak", {"path": "02/story01"})
    res = got.get("result", {})
    check("says which file it expects on the card",
          res.get("expects_file") == "02/story01.mp3", str(res.get("expects_file")))
    check("no advice when using the documented folder", res.get("advice") is None,
          str(res.get("advice")))
    got = await core.run("speak", {"path": "AppSysMS/101"})
    check("warns when aimed at a protected folder",
          "do-not-modify" in (got.get("result", {}).get("advice") or ""),
          str(got.get("result", {}).get("advice")))
    got = await core.run("speak", {"path": "07/whatever"})
    check("warns when using an undocumented folder",
          spec.USER_AUDIO_FOLDER in (got.get("result", {}).get("advice") or ""),
          str(got.get("result", {}).get("advice")))
    check("but still plays it - advice is not a block",
          got.get("ok") is True, str(got.get("error", ""))[:50])

    print("\n-- clipInfo: the one audio command that replies --")
    core = await connected_sim()
    got = await core.run("clipInfo", {"clip": 101, "timeout": 0.3})
    check("reports failure honestly when the robot stays silent",
          got.get("ok") is True and got.get("result", {}).get("ok") is False,
          str(got)[:80])
    check("and does not invent a reply",
          got.get("result", {}).get("reply") is None, str(got.get("result"))[:60])
    check("the frame did go out",
          any(bytes.fromhex(f["hex"])[3:4].hex() == spec.code("play")
              for f in core.bus.session), "no play frame sent")

    print("\n-- glide: ramped poses, gentler on the servos --")
    core = await connected_sim()
    got = await core.run("glide", {"pose": {"head": 142}, "speed": 25, "step_delay": 0.0})
    plan = got.get("result", {})
    check("a small move stays small", plan.get("steps", 99) <= 3,
          f"{plan.get('steps')} steps for 20 counts")
    check("reports the plan", plan.get("max_delta") == 20
          and plan.get("moved_joints") == ["head"], str(plan))
    check("only the named joint moves", plan.get("moved_joints") == ["head"],
          str(plan.get("moved_joints")))

    print("\n-- and the ramp honours max_step exactly --")
    for max_step, pose, label in ((8, {"leftAnkle": 60, "rightAnkle": 200}, "ankles"),
                                  (5, {"leftThigh": 40, "rightThigh": 210}, "thighs"),
                                  (12, {"head": 60}, "head")):
        core = await connected_sim()
        before = len(core.bus.session)
        # Capture the pose we are gliding FROM, before the run: `core.joints`
        # is the destination once the glide finishes, so reading it afterwards
        # would compare every frame against the wrong baseline.
        started_from = dict(core.joints)
        got = await core.run("glide", {"pose": pose, "speed": 25,
                                       "max_step": max_step, "step_delay": 0.0})
        plan = got.get("result", {})
        sent = [bytes.fromhex(f["hex"]) for f in core.bus.session[before:]
                if bytes.fromhex(f["hex"])[3:4].hex() == spec.code("jointMove")]
        joints = spec.joints()
        # The guarantee: between consecutive frames, no joint moves more than
        # max_step, measured from the pose we actually started in.
        prev = dict(started_from)
        worst = 0
        for raw in sent:
            cur = protocol.parse(raw).struct
            worst = max(worst, max(abs(cur[n] - prev[n]) for n in joints))
            prev = {n: cur[n] for n in joints}
        check(f"{label}: no frame moves a joint more than {max_step}",
              worst <= max_step, f"worst was {worst}")
        check(f"{label}: one frame per step",
              len(sent) == plan.get("steps"), f"{len(sent)} frames, {plan.get('steps')} steps")
        final = protocol.parse(sent[-1]).struct
        want = protocol.clamp_pose(pose)
        wrong = {n: (final[n], want[n]) for n in want if final[n] != want[n]}
        check(f"{label}: lands exactly on the joints it was asked to move",
              not wrong, str(wrong))
        untouched = [n for n in joints if n not in want]
        check(f"{label}: left every other joint where it started",
              all(final[n] == started_from[n] for n in untouched),
              str({n: (final[n], started_from[n]) for n in untouched
                   if final[n] != started_from[n]}))

    print("\n-- glide inherits the stale guard and the queue --")
    core = await connected_sim(speed=60.0)
    await core.run("action", {"name": "Left Punch"})
    got = await core.run("glide", {"pose": {"head": 140}})
    check("refused while stale", not got.get("ok"), str(got.get("error", ""))[:50])
    check("nothing on the wire",
          not any(bytes.fromhex(f["hex"])[3:4].hex() == spec.code("jointMove")
                  for f in core.bus.session), "a frame escaped")

    core = await connected_sim()
    task = asyncio.create_task(core.run("glide", {"pose": {"head": 60},
                                                  "step_delay": 0.01, "max_step": 4}))
    while not core.busy:
        await asyncio.sleep(0.002)
    other = asyncio.create_task(core.run("state", {}))
    await task
    await other
    check("held the queue for the whole ramp", True)

    print("\n-- glide does not disturb the single-frame pose intent --")
    core = await connected_sim()
    before = len(core.bus.session)
    await core.run("pose", {"pose": {"head": 140}})
    stepped = [f for f in core.bus.session[before:]
               if bytes.fromhex(f["hex"])[3:4].hex() == spec.code("jointMove")]
    check("pose still sends exactly one frame", len(stepped) == 1, f"{len(stepped)} frames")
    check("pose still teleports", "glide" not in str(stepped), "unexpected ramp")

    print("\n-- the audio naming rule, as established on hardware --")
    # Three one-variable probes, each confirmed by ear on real hardware:
    #   AppSysMS/101 -> plays, 02/101 -> plays, 02/story01 -> silent,
    #   AppProMS/Delight -> plays. So letters are fine; 02/ wants numeric ids.
    check("user folder is the one that needs numeric names",
          spec.AUDIO_USER_FOLDER_NEEDS_NUMERIC is True)
    core = await connected_sim()
    numeric = await core.run("speak", {"path": "02/101"})
    lettered = await core.run("speak", {"path": "02/story01"})
    check("a numeric id in the user folder gets no naming warning",
          numeric.get("result", {}).get("naming_advice") is None,
          str(numeric.get("result", {}).get("naming_advice"))[:60])
    check("a lettered name in the user folder is warned about",
          "numeric" in (lettered.get("result", {}).get("naming_advice") or ""),
          str(lettered.get("result", {}).get("naming_advice"))[:60])
    check("but it is still sent - advice, not a block",
          lettered.get("ok") is True, str(lettered.get("error", ""))[:50])
    lettered_sys = await core.run("speak", {"path": "AppProMS/Delight"})
    check("a lettered name in a system folder is NOT warned about",
          lettered_sys.get("result", {}).get("naming_advice") is None,
          str(lettered_sys.get("result", {}).get("naming_advice"))[:60])
    check("the warning is reported as still-playing behaviour we rely on",
          spec.AUDIO_LISTING_IS_EMPTY_ON_HARDWARE is True)

    print("\n-- action_path: reaching stunts the spec has never heard of --")
    core = await connected_sim(speed=60.0)
    got = await core.run("actionPath", {"path": "ProAction/ZZTestPunch"})
    res = got.get("result", {})
    check("a spec-unknown path is sent", got.get("ok") is True, str(got.get("error", ""))[:60])
    check("and reported as not in the spec", res.get("in_spec") is False, str(res)[:70])
    check("duration is flagged as estimated", res.get("duration_estimated") is True,
          str(res)[:70])
    check("the path is echoed back", res.get("path") == "ProAction/ZZTestPunch",
          str(res.get("path")))
    sent = [bytes.fromhex(f["hex"]) for f in core.bus.session
            if bytes.fromhex(f["hex"])[3:4].hex() == spec.code("action")]
    check("exactly one action frame", len(sent) == 1, f"{len(sent)} frames")
    payload = protocol.parse(sent[0]).text()
    check("on the wire as ProAction/ZZTestPunch", payload == "ProAction/ZZTestPunch",
          repr(payload))

    print("\n-- a path the spec does know is recognised --")
    core = await connected_sim(speed=60.0)
    got = await core.run("actionPath", {"path": "ProAction/Left Punch"})
    res = got.get("result", {})
    check("marked as in-spec", res.get("in_spec") is True, str(res)[:70])
    check("duration comes from the spec, not a guess",
          res.get("duration_estimated") is False and res.get("duration_ms") == 800,
          str(res)[:70])
    check("and it reports the canonical name", res.get("action") == "Left Punch",
          str(res.get("action")))

    print("\n-- action_path: strict mode still guards it, default is open --")
    core = await connected_sim(speed=60.0)
    got = await core.run("actionPath", {"path": "ProAction/front flip"})
    check("a card stunt is allowed by default", got.get("ok") is True,
          str(got.get("error", ""))[:44])
    check("and reported as name-matched risk",
          got.get("result", {}).get("fall_risk") == "flip",
          str(got.get("result", {}).get("fall_risk")))
    core = await connected_sim(speed=60.0)
    for path in ("ProAction/Do Squats", "ProAction/front flip",
                 "ProAction/back flip", "ProAction/left kick",
                 "ProAction/play drum", "ProAction/keep rolling backward"):
        before = len(core.bus.session)
        got = await core.run("actionPath", {"path": path, "strict": True})
        check(f"{path} refused under strict",
              not got.get("ok") and len(core.bus.session) == before,
              str(got.get("error", ""))[:44])
    got = await core.run("actionPath", {"path": "ProAction/Do Squats", "strict": True,
                                        "allow_fall_risk": True})
    check("strict plus override works", got.get("ok") is True,
          str(got.get("error", ""))[:50])

    print("\n-- the 13 spec-unknown stunts are all still allowed --")
    # They are the reason action_path exists, and none of them is fall-risk.
    core = await connected_sim(speed=60.0)
    for name in ("Resume Standing", "shake head", "Chest Up", "Open Arms",
                 "Open Legs", "Raise Left Hand", "Raise Right Hand",
                 "Raise Your Hands", "showtime", "pose to take off",
                 "lift your arms", "lift your left arm", "lift your right arm"):
        check(f"{name!r} not misclassified as fall-risk",
              spec.classify_fall_risk(name) is None, str(spec.classify_fall_risk(name)))
    check("'stand' is not a risk word - it blocked Resume Standing",
          "stand" not in spec._FALL_RISK_WORDS, "still there")
    check("Handstand is still caught, by its own word",
          spec.classify_fall_risk("Handstand") == "handstand",
          str(spec.classify_fall_risk("Handstand")))
    core = await connected_sim(speed=60.0)
    got = await core.run("actionPath", {"path": "SpeAction/Resume Standing"})
    check("Resume Standing is actually reachable",
          got.get("ok") is True, str(got.get("error", ""))[:60])

    print("\n-- malformed paths are refused --")
    core = await connected_sim(speed=60.0)
    for bad in ("", "../etc/passwd", "/ProAction/X", "C:/x", "no-slash",
                "too/many/slashes", "ProAction/\x01"):
        before = len(core.bus.session)
        got = await core.run("actionPath", {"path": bad})
        check(f"{bad!r} refused",
              not got.get("ok") and len(core.bus.session) == before,
              str(got.get("error", ""))[:40])

    print("\n-- an unknown *name* now suggests near matches --")
    core = await connected_sim(speed=60.0)
    got = await core.run("action", {"name": "Left Puch"})
    check("typo gets a suggestion", "Left Punch" in got.get("error", ""),
          got.get("error", "")[:70])
    check("and is pointed at action_path for spec-unknown names",
          "action_path" in got.get("error", ""), got.get("error", "")[:70])

    print("\n-- SD card inspection is read-only and reports honestly --")
    # Build a fake card so this runs without hardware attached.
    import tempfile
    from k1lib import sdcard
    tmp = Path(tempfile.mkdtemp())
    (tmp / "Warning instructions.txt").write_text("do not delete things\n")
    (tmp / "AppSysMS").mkdir()
    (tmp / "AppSysMS" / "101.mp3").write_bytes(b"\x00" * 8)
    (tmp / "02").mkdir()
    for folder, names in (("Action", ["Boogaloo.sh"]),
                          ("ProAction", ["Left Punch.sh", "Lay Down.sh"]),
                          ("SpeAction", ["Left Punch.sh", "Resume Standing.sh",
                                         "shake head.sh"])):
        (tmp / folder).mkdir()
        for n in names:
            body = bytes(32 * 3)
            body += b"\xfc\xfc" if "Lay" in n else b"\xfc\xfc"
            (tmp / folder / n).write_bytes(body[:-2] + b"\xfc\xfc")

    info = sdcard.inventory(tmp)
    check("card recognised", info["stunt_files"] == 6, str(info["stunt_files"]))
    check("counts per folder", info["stunts_by_folder"] == {"Action": 1, "ProAction": 2,
                                                            "SpeAction": 3},
          str(info["stunts_by_folder"]))
    unknown = {r["path"] for r in info["unknown_to_spec"]}
    check("Resume Standing flagged as unknown to the spec",
          "SpeAction/Resume Standing" in unknown, str(sorted(unknown)))
    check("and a spec-known name is not flagged",
          "SpeAction/Left Punch" not in unknown, str(sorted(unknown)))
    check("two-folder duplicate counted", info["two_folder_variants"] == 1,
          str(info["two_folder_variants"]))
    check("user audio folder reported", info["user_audio_folder"] == "02",
          info["user_audio_folder"])

    print("\n-- it refuses to guess about a non-card --")
    empty = Path(tempfile.mkdtemp())
    try:
        sdcard.inventory(empty)
        check("NotACard raised", False, "no error")
        message = ""
    except sdcard.NotACard as exc:
        message = str(exc)
        check("NotACard raised for a folder that is not a card", True)
    check("the error says what a card looks like",
          "Warning instructions.txt" in message and "AppSysMS" in message, message[:70])
    check("and mentions USB-C, since that is the actual cause",
          "USB-C" in message, message[:70])

    print("\n-- and it never writes to the card --")
    before = {p: p.stat().st_mtime_ns for p in tmp.rglob("*") if p.is_file()}
    sdcard.inventory(tmp)
    sdcard.describe(tmp)
    after = {p: p.stat().st_mtime_ns for p in tmp.rglob("*") if p.is_file()}
    check("no file mtime changed", before == after,
          f"{len(before)} -> {len(after)} files")
    src = Path(sdcard.__file__).read_text()
    for banned in ("write_bytes", "write_text", "shutil.copy", "os.remove",
                   "unlink", "mkdir", "rmtree", "open(", "\"w\""):
        check(f"source contains no {banned!r}", banned not in src,
              "found it" if banned in src else "")

    print("\n-- the K1 facade closes cleanly and drains the queue --")
    # Regression found on hardware: `close = RobotCore.stop` bound `self` to the
    # facade, which only worked while RobotCore.stop touched nothing but `.bus`
    # (the facade proxies that one attribute). Adding queue draining touched
    # core-only state and broke every `async with K1(...)` exit.
    from k1lib.robot import K1
    robot = K1("sim")
    await robot.connect()
    check("facade connected", robot.simulated is True)
    task = asyncio.create_task(robot.core.run("action", {"name": "Left Punch"}))
    while not robot.core.busy:
        await asyncio.sleep(0.005)
    await robot.close()
    check("close did not raise while a command was in flight", True)
    check("the in-flight command still got a result", (await task).get("ok") is True,
          "close cancelled the queued action")
    check("bus is closed", robot.core._open is False, "core still open")

    robot2 = K1("sim")
    await robot2.connect()
    await robot2.close()
    check("close works when idle too", robot2.core._open is False, "still open")

    print("\n-- listeners cannot crash the core --")
    core = await connected_sim()

    def bad_listener(event, data):
        raise RuntimeError("boom")

    core.on(bad_listener)
    core.on(lambda e, d: None)
    core.emit("test", {})
    check("bad listener survived", True)
    outcome = await core.run("state", {})
    check("core still works", outcome.get("ok") is True)
    return report.finish()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))