"""RobotCore - the `robotd` of this stack.

The only object that touches a bus. Everything above it (CLI, gamepad, a script,
an LLM planner) sends *intents* - "do this thing" - and never sees a frame.

Transports are swappable, so the identical intent stream runs against the
simulator with no hardware attached and against the real robot over BLE.
"""
from __future__ import annotations

import asyncio
import difflib
import logging
import time
from typing import Any, Callable, Iterable

from . import protocol, spec
from .bus import BleBus, Bus, SimBus

log = logging.getLogger("k1.core")

# Continuous moves. Codes come from robot.json's command.Move table, which is
# NOT the same as the bare type names: `spec.code("forward")` is 33, but walking
# forward uses 01 (`moveNorth`). Mixing the two silently commands the wrong
# motion, so this map is spelled out and a test pins it.
DIRECTIONS = {
    "forward": "01",
    "backward": "05",
    "left": "07",
    "right": "03",
    "turn left": "08",
    "turn right": "02",
}
MOVE_MIN_MS, MOVE_MAX_MS = 500, 10000
# The spec's floor is 500ms, but that is not a usable walk. Observed on
# hardware: the robot needs roughly a second to drop into its locomotion stance
# before it travels at all, so anything under ~2s is mostly stance transition.
# A 4000ms move - 1s to get into position, ~3s of running - walks properly.
MOVE_SETTLE_MS = 1000
MOVE_USEFUL_MS = 4000

# Action completion. The robot reports progress faithfully *as long as we send
# one command at a time*: run serially, `Left Punch` gives a clean 50 then 100.
# Send anything else mid-stunt - a state poll, or a second action - and the
# feedback degrades, which is how a punch went missing during testing. So the
# rule this library enforces is discipline, not distrust: one command in flight,
# wait for real completion, never poll while the robot is busy.
#
# The timeout paths below are a backstop for a robot that has genuinely stopped
# reporting, and they say which signal fired so a caller never has to guess.
ACTION_QUIET_S = 2.0   # no progress for this long => treat as finished
# ...but the firmware can send as few as two packets (50% then 100%), so the gap
# between them grows with the stunt: a flat 2s window cut `Left Kick` off at
# 4.1s of its declared 5.4s. The window therefore scales with the declared
# clock, leaving margin over the half-clock gap that pattern produces while
# still finishing well before the deadline's slack.
ACTION_QUIET_FRACTION = 0.75
ACTION_GRACE_S = 10.0  # minimum wait, even if the declared duration is short
# A fixed cap cannot work: the card's longest dance is declared at 81s. The
# deadline is therefore the larger of the grace floor and the action's own
# declared clock plus this slack. Without it, a dance longer than the floor
# would report `timeout` while still animating, release the queue, and let the
# next command be dropped mid-stunt.
ACTION_SLACK_S = 15.0
DELAY = 0.05  # duration.delay in the spec - settle time after a joint write
STEP_KEYS = ("action", "move", "pose", "delta", "say", "stand", "wait")

# Routine files use friendly keys; intent handlers use parameter names. Map
# explicitly so a mismatch is a clear error here, not a TypeError at runtime.
STEP_INTENTS = {
    "action": lambda s: ("action", {
        "name": s["action"],
        **({"wait": s["wait"]} if "wait" in s else {}),
        **({"strict": True} if s.get("strict") else {}),
        **({"allow_fall_risk": True} if s.get("allowFallRisk") else {}),
        # The default deadline is the *declared* clock + slack, and some
        # declarations are an order of magnitude short (`Play Dead` 1.68s vs
        # 16.78s measured), which would cut a routine off before the stunt
        # ends. A step may say how long it is really willing to wait.
        **({"grace_s": float(s["grace"])} if "grace" in s else {}),
    }),
    "move": lambda s: ("move", {"direction": s["move"], "ms": s.get("ms", 1000)}),
    "pose": lambda s: ("pose", {"pose": s["pose"], "speed": s.get("speed", 40)}),
    "delta": lambda s: ("delta", {"delta": s["delta"], "speed": s.get("speed", 40)}),
    "say": lambda s: ("say", {"clip": s["say"]}),
    # Your own audio, by on-device path. The only route to arbitrary words:
    # generate speech, copy it to 02/<number>.mp3 over USB-C, then name it here.
    "speak": lambda s: ("speak", {"path": s["speak"]}),
    # Card-only stunts, which robot.json never names - e.g. "SpeAction/showtime".
    "actionPath": lambda s: ("actionPath", {
        "path": s["actionPath"],
        **({"strict": True} if s.get("strict") else {}),
    }),
    # Ramped joint motion: a pose reached in steps of at most `maxStep` counts,
    # instead of one 17-joint jump. The gentlest way to move joints on purpose.
    "glide": lambda s: ("glide", {
        "pose": s["glide"],
        "speed": s.get("speed", 30),
        "max_step": s.get("maxStep", 8),
        "step_delay": 0.0 if s.get("fast") else None,
    }),
    # Reset to documented neutral. Put this after any action/move step if the
    # routine keeps going with pose steps - see RobotCore._stale. Off by
    # default because the vendor neutral leans; a routine must opt in with
    # `allowLeaningNeutral` to use it.
    "stand": lambda s: ("stand", {
        "speed": s.get("speed", 40),
        "allow_leaning_neutral": bool(s.get("allowLeaningNeutral", False)),
    }),
}


class IntentError(ValueError):
    """Bad intent: unknown action, malformed pose, out-of-range value."""


class _Collector:
    """Accumulates a list-type reply: repeated items, terminated by `done`."""

    __slots__ = ("collect_type", "done_type", "items", "finished")

    def __init__(self, collect_type: str, done_type: str):
        self.collect_type = collect_type
        self.done_type = done_type
        self.items: list[bytes] = []
        self.finished = asyncio.Event()


class RobotCore:
    def __init__(self, bus: Bus | None = None):
        self.bus: Bus = bus or SimBus()
        self.joints: dict[str, int] = protocol.joint_struct()
        self.state: dict | None = None
        self.listeners: list[Callable[[str, dict], None]] = []
        self._progress = asyncio.Event()
        self._action: str | None = None
        self._reply_count = 0
        self._open = False
        # Newest notification per type code, so a request can wait for its own
        # answer instead of guessing from the packet count.
        self._replies: dict[str, protocol.Packet] = {}
        # Streams being collected right now, for the list-type queries
        # (audioNames, actionNames, folderNames, userNames).
        self._collectors: list[_Collector] = []
        # Folders the robot has actually told us about, so `speak` can be honest
        # about whether a path is aimed at a real location.
        self._folders_seen: set[str] = set()
        # True when the robot's real pose has changed without us being told the
        # new values, so `self.joints` no longer describes the hardware.
        self._stale = False
        # Progress tracking for action completion.
        self._progress_seen = False
        self._last_progress: float | None = None
        # A 100% packet only counts if the robot already reported this action
        # *running*. The percent packet carries no action id, so without this a
        # late 100 from the previous stunt lands on the new wait and ends it
        # instantly - observed on hardware as a 9s dance completing in 47ms.
        self._saw_partial = False
        self.require_start_packet = True
        self._action_started: float | None = None
        self._action_trace: list[tuple[float, int]] = []
        self._action_peak: int | None = None
        self._firmware_ms: float | None = None
        # Held for the whole duration of a command, including the time an action
        # spends animating, so nothing else can interleave with it.
        self._command_lock = asyncio.Lock()
        self._tasks: set[asyncio.Task] = set()
        # Completion backstop timings, per instance so tests can shrink them
        # instead of waiting out the real 2s/10s windows on every fault-injection
        # path. Production code never touches these.
        self.action_quiet_s = ACTION_QUIET_S
        self.action_grace_s = ACTION_GRACE_S
        self.action_slack_s = ACTION_SLACK_S
        # Deadline of the action currently being waited on, so `stop()` can let
        # an 81s dance finish instead of closing the radio at a fixed 12s.
        self._action_deadline = 0.0
        # Multiplies every internal pacing sleep. Production is always 1.0, where
        # a 4-second walk really does take four seconds. Tests set it low so a
        # move doesn't have to be waited out - the *frames* are identical either
        # way, only the wall clock changes. Deadlines and timeouts are NOT scaled,
        # so completion logic behaves identically.
        self.time_scale = 1.0

    async def _nap(self, seconds: float):
        await asyncio.sleep(max(0.0, seconds * self.time_scale))

    def _spawn(self, coro) -> asyncio.Task:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    # -- lifecycle ---------------------------------------------------------
    async def start(self) -> str:
        self.bus.on_packet = self._on_packet
        label = await self.bus.open()
        self._open = True
        self.emit("hello", {"bus": self.bus.name, "label": label})
        return label

    async def stop(self):
        # Let anything still queued or mid-animation finish before the radio
        # closes, or a robot could be left half-way through a stunt. The wait
        # honours the action's own deadline: a fixed 12s would cut off an 81s
        # dance that `_await_action` is correctly still waiting for.
        drain_s = max(self.action_grace_s, self._action_deadline) + 2
        for task in list(self._tasks):
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=drain_s)
            except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
                pass
        await self._when_idle(timeout=drain_s)
        await self.bus.close()
        self._open = False

    @property
    def simulated(self) -> bool:
        return isinstance(self.bus, SimBus)

    def on(self, callback: Callable[[str, dict], None]):
        self.listeners.append(callback)

    def emit(self, event: str, data: dict):
        for callback in self.listeners:
            try:
                callback(event, data)
            except Exception:  # a bad listener must not take down the core
                log.exception("listener failed")

    # -- command queue -----------------------------------------------------
    # The robot runs one command at a time. Driven serially it behaves well;
    # the moment a second command lands mid-stunt, the feedback degrades and
    # the action is silently dropped. That is not something to ask every caller
    # to remember, so it is an invariant here: exactly one command is in flight
    # at a time and everything else waits its turn. Requests queue in arrival
    # order rather than being refused.
    @property
    def busy(self) -> bool:
        """True while a command holds the queue."""
        return self._command_lock.locked()

    @property
    def queued(self) -> int:
        """Callers waiting for their turn."""
        return len(getattr(self._command_lock, "_waiters", []) or [])

    async def _when_idle(self, timeout: float | None = None) -> bool:
        """Wait for the queue to drain. False if it did not drain in time."""
        if timeout is None:
            await self._command_lock.acquire()
            self._command_lock.release()
            return True
        try:
            await asyncio.wait_for(self._command_lock.acquire(), timeout)
        except asyncio.TimeoutError:
            return False
        self._command_lock.release()
        return True

    # -- inbound packets ---------------------------------------------------
    def _on_packet(self, pkt: protocol.Packet):
        # every notification counts as inbound traffic, whether or not we
        # understand it - i_readPose uses this to tell "no reply" from "reply"
        self._reply_count += 1
        self._replies[pkt.type] = pkt

        # List queries: items arrive as repeated notifications of the queried
        # type and the stream ends with `done` (fa). Keep the collector rather
        # than only the newest packet, or a 27-item list would collapse to one.
        for collector in self._collectors:
            if pkt.type == collector.collect_type:
                collector.items.append(pkt.data)
            elif pkt.type == collector.done_type:
                collector.finished.set()

        if pkt.type == spec.code("state") and pkt.struct:
            self.state = pkt.struct
            self.emit("state", pkt.struct)
        elif pkt.type == spec.code("action") and len(pkt.data) == 1:
            pct = int(pkt.data[0])
            now = time.monotonic()
            self._progress_seen = True
            self._last_progress = now
            if self._action_started is not None:
                self._action_trace.append(
                    (round((now - self._action_started) * 1000.0, 1), pct))
                self._action_peak = (pct if self._action_peak is None
                                     else max(self._action_peak, pct))
            self.emit("progress", {"action": self._action, "percent": pct})
            if pct >= 100:
                if self._saw_partial or not self.require_start_packet:
                    self._progress.set()
                    if self._firmware_ms is None and self._action_started is not None:
                        self._firmware_ms = (now - self._action_started) * 1000.0
                else:
                    # Keep waiting: an unprovoked 100 cannot mean this action
                    # finished, and treating it as completion invents a timing.
                    log.warning(
                        "ignoring progress-100 for %s with no start packet "
                        "(%.0fms after the write); it cannot be this action "
                        "finishing", self._action,
                        ((now - self._action_started) * 1000.0)
                        if self._action_started else -1.0)
            else:
                self._saw_partial = True
                self._progress.clear()

    # -- outbound ----------------------------------------------------------
    async def _send(self, type_name: str, data=None, *, _held: bool = False):
        """Write one frame, taking the command queue unless we already hold it.

        `_held=True` is for commands issued from inside a command that is
        already running (the `stop` that ends a move, say). Without it those
        would deadlock on a lock the same task is holding.
        """
        if not _held:
            async with self._command_lock:
                return await self.bus.write(protocol.packet(spec.code(type_name), data))
        return await self.bus.write(protocol.packet(spec.code(type_name), data))

    # -- intents -----------------------------------------------------------
    async def i_state(self, during_action: str = "wait", **_) -> dict:
        """Read battery/volume/flags.

        `during_action` controls what happens if a stunt is animating:

        - `"wait"` (default) - queue behind it. This is not politeness, it is
          correctness: verified on hardware, a state frame sent mid-action
          **suppresses that action's progress notifications**, so an impatient
          poll silently breaks completion detection for the stunt. The command
          queue makes waiting the default, so a client can poll as often as it
          likes and never damage a running routine.
        - `"force"` - send anyway and accept the consequence.
        - `"cached"` - return the last known state without asking the robot.
        """
        if during_action == "cached":
            return dict(self.state or {})
        if during_action == "force":
            await self._send("state", _held=True)
        else:
            await self._send("state")  # queues behind anything in flight
        for _ in range(20):
            await asyncio.sleep(0.05)
            if self.state:
                return dict(self.state)
        return {}

    async def _ask(self, type_name: str, timeout: float = 1.5) -> str | None:
        """Send a query and wait for the notification that answers it.

        Returns the decoded text, or None if nothing came back in time. Callers
        must handle None - a silent robot is a real possibility, not an
        exception (jointSync is the extreme case).
        """
        code = spec.code(type_name)
        stale = self._replies.get(code)
        await self._send(type_name)
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            reply = self._replies.get(code)
            if reply is not None and reply is not stale:
                return reply.text().strip()
            await asyncio.sleep(0.05)
        return None

    async def i_info(self, timeout: float = 1.5, **_) -> dict:
        """Read-only identification: model, firmware version, build date.

        These are the only commands in the whole spec that make the robot report
        something about itself, and none of them move a joint. Verified on real
        hardware: kind `K1`, version `VER:3.04L`, date `SH2022-08-15`.
        """
        out: dict[str, str | None] = {}
        for label in ("kind", "version", "date"):
            out[label] = await self._ask(label, timeout)
        out["ok"] = out["kind"] is not None
        return out

    async def i_list(self, kind: str = "audioNames", timeout: float = 3.0, **_) -> dict:
        """Ask the robot to enumerate one of its own lists.

        `audioNames` (18), `actionNames` (14), `folderNames` (16) and
        `userNames` (10) all work the same way: the robot replies with a stream
        of notifications of the same type, then `done` (fa).

        This matters because robot.json is a build-time document. It can tell us
        which clips the *firmware* shipped with, but not what the phone app has
        downloaded onto the robot since. Only the robot knows that.
        """
        found = spec.type_by_code(spec.code(kind)) if spec.code(kind) else None
        if not found or not found[1].get("list"):
            raise IntentError(
                f"{kind!r} is not a list type; try one of "
                f"{[n for n in ('userNames', 'actionNames', 'folderNames', 'audioNames')]}"
            )
        collector = _Collector(spec.code(kind), spec.code("done"))
        self._collectors.append(collector)
        try:
            async with self._command_lock:
                await self.bus.write(protocol.packet(spec.code(kind)))
            try:
                await asyncio.wait_for(collector.finished.wait(), timeout)
            except asyncio.TimeoutError:
                log.warning("%s list did not terminate with 'done' after %ss; "
                            "reporting what arrived", kind, timeout)
        finally:
            if collector in self._collectors:
                self._collectors.remove(collector)

        names = [item.decode("utf-8", "replace") for item in collector.items]
        if kind == "folderNames":
            self._folders_seen.update(names)
        return {
            kind: names,
            "count": len(names),
            # Say so explicitly: a truncated list must never look complete.
            "complete": collector.finished.is_set(),
        }

    async def i_audio(self, timeout: float = 3.0, **_) -> dict:
        """What the robot can actually say: its own list, plus the spec's.

        The two differ, and the difference is the interesting part - anything the
        app downloaded will appear in `available` but not in `declared`.
        """
        listed = await self.i_list("audioNames", timeout)
        available = listed.get("audioNames", [])
        declared = [str(c) for c in spec.audio_clips()]

        # The robot reports full paths ("AppSysMS/205"); the spec declares bare
        # ids (205). Compare the id part, otherwise everything looks extra.
        def clip_id(entry: str) -> str:
            return entry.rsplit("/", 1)[-1]

        known = set(declared)
        extra = [n for n in available if clip_id(n) not in known]
        return {
            "declared": declared,
            "declared_count": len(declared),
            "on_device": available,
            "on_device_count": len(available),
            "list_complete": listed.get("complete"),
            # Content the app downloaded onto the robot, absent from robot.json.
            "not_in_spec": extra,
            "folders": spec.audio_folders(),
        }

    async def i_folders(self, timeout: float = 3.0, **_) -> dict:
        """Ask the robot which audio folders it has, and audio clips per folder.

        This is the discovery step for playing your own audio: robot.json is a
        build-time document and cannot know what the phone app put on the robot.
        Pair it with `list(kind="audioNames")` - entries look like
        "Folder/name", so the folder prefixes tell you where files live.
        """
        listed = await self.i_list("folderNames", timeout)
        folders = listed.get("folderNames", [])
        names = await self.i_list("audioNames", timeout)
        entries = names.get("audioNames", [])
        by_folder: dict[str, list[str]] = {}
        for entry in entries:
            folder, _, name = entry.rpartition("/")
            by_folder.setdefault(folder or "(root)", []).append(name)
        return {
            "folders": folders,
            "folders_complete": listed.get("complete"),
            "audio": by_folder,
            "audio_complete": names.get("complete"),
            "total_audio": len(entries),
        }

    async def i_clip_info(self, clip: int = 101, timeout: float = 2.0, **_) -> dict:
        """Ask about a clip with `play` (ee), the one audio command that replies.

        `ee` is listed as "play message", takes a *number* and expects a data
        reply - unlike `audio`, which takes a path and is fire-and-forget. That
        makes it the most likely route to something richer than a canned clip,
        possibly a message/story index. It is also unverified: found in the spec
        and the SDK, never run against hardware. Report what comes back rather
        than assuming it is a description.

        Read-only in intent, but it may also start playback - it is audio, so
        nothing moves.
        """
        code = spec.code("play")
        stale = self._replies.get(code)
        await self._send("play", int(clip))
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            reply = self._replies.get(code)
            if reply is not None and reply is not stale:
                return {"clip": int(clip), "reply": reply.data.decode("utf-8", "replace"),
                        "ok": True}
            await asyncio.sleep(0.05)
        return {"clip": int(clip), "reply": None, "ok": False,
                "error": "no reply to `play`; unverified path, the robot may "
                         "simply not implement a reply for it"}

    async def i_volume(self, level: int = 100, **_) -> dict:
        level = max(0, min(140, int(level)))
        await self._send("volume", level)
        return {"volume": level}

    async def i_autoStand(self, on: bool = True, **_) -> dict:
        await self._send("autoStand", bool(on))
        return {"autoStand": bool(on)}

    async def i_autoOff(self, on: bool = True, **_) -> dict:
        await self._send("autoOff", bool(on))
        return {"autoOff": bool(on)}

    async def i_action(self, name: str, wait: bool = True,
                       allow_fall_risk: bool = False, strict: bool = False,
                       grace_s: float | None = None, **_) -> dict:
        """Play a canned stunt or dance by its name in robot.json.

        Factory stunts are played by default. Every one of the 130 `.sh` scripts
        on the card is a vendor-authored trajectory, written and factory-tested
        for this robot and its balance controller - second-guessing them on the
        strength of a keyword match was wrong, and `Push Ups` and `Left Kick`
        (both "fall-risk" by name) have both been watched performing cleanly.

        What is *not* factory is our own joint writing, and that is guarded
        separately by the stale-pose check on `pose`, `glide` and friends.

        `strict=True` restores the old conservative behaviour: refuse anything
        the name-based classifier dislikes unless `allow_fall_risk` is also set.
        The classification is always reported as `fall_risk`, either way.

        `grace_s` overrides the computed completion deadline for this call. The
        default is `max(action_grace_s, duration_ms/1000 + action_slack_s)`, so
        a long dance is waited out instead of being cut off at the 10s floor.
        """
        entry = spec.actions().get(name)
        if not entry:
            near = difflib.get_close_matches(name, list(spec.actions()), n=3, cutoff=0.6)
            hint = f" Did you mean {near}?" if near else ""
            raise IntentError(
                f"unknown action {name!r}.{hint} For a name the spec has never "
                f"heard of - one you put on the SD card yourself - use "
                f"action_path instead."
            )
        if strict:
            self._check_fall_risk(name, allow_fall_risk)
        result = await self._perform_action(entry["path"], name,
                                            entry["duration_ms"], wait,
                                            grace_s=grace_s)
        word = spec.classify_fall_risk(name)
        result["fall_risk"] = word
        result["factory_stunt"] = True
        if name in spec.VERIFIED_SAFE:
            result["verified"] = spec.VERIFIED_SAFE[name]
        return result

    async def i_action_path(self, path: str, wait: bool = True,
                            allow_fall_risk: bool = False, strict: bool = False,
                            grace_s: float | None = None, **_) -> dict:
        """Play a stunt by its raw card path, e.g. "ProAction/ZZTestPunch".

        robot.json only ever names `Action/` and `ProAction/`, but the SD card
        holds 130 stunt scripts and `SpeAction/` is absent from the spec
        entirely. This intent asks the firmware for a path directly, which is how
        you reach those - and how we test whether it will serve a name the spec
        has never heard of.

        The fall-risk block is off by default, as with `action`: the file is a vendor
        trajectory like any other. Pass `strict=True` to opt back into refusing
        name-matched stunts.
        """
        cleaned = str(path).strip().replace("\\", "/")
        if not cleaned:
            raise IntentError("path is empty")
        if ".." in cleaned.split("/"):
            raise IntentError(f"path {path!r} contains '..'")
        if cleaned.startswith("/") or ":" in cleaned:
            raise IntentError(f"path {path!r} must be relative, like 'ProAction/Left Punch'")
        if any(ord(ch) < 32 for ch in cleaned):
            raise IntentError(f"path {path!r} contains control characters")
        if cleaned.count("/") != 1:
            raise IntentError(f"path {path!r} should be 'Folder/name'")

        folder, name = cleaned.split("/", 1)
        if strict:
            self._check_fall_risk(name, allow_fall_risk)

        known = {e["path"].lower(): n for n, e in spec.actions().items()}
        recognised = known.get(cleaned.lower())

        # No duration is known for a spec-unknown path, so completion falls back
        # to the progress signal alone rather than the spec's clock.
        duration = spec.duration_ms(recognised) if recognised else spec.UNKNOWN_ACTION_MS
        result = await self._perform_action(cleaned, name, duration, wait,
                                            grace_s=grace_s)
        result.update({
            "path": cleaned,
            "in_spec": recognised is not None,
            "duration_estimated": recognised is None,
            "fall_risk": spec.classify_fall_risk(name),
            "factory_stunt": True,
        })
        if recognised:
            result["action"] = recognised
        return result

    def _check_fall_risk(self, name: str, allow_fall_risk: bool):
        word = spec.classify_fall_risk(name)
        if not word or allow_fall_risk:
            return
        alternatives = [n for n in spec.safe_actions()[:3]]
        raise IntentError(
            f"refusing {name!r}: it matches {word!r} and risks toppling the robot. "
            f"Pass allow_fall_risk=True if you really mean it. "
            f"Safest short options: {alternatives}"
        )

    async def _perform_action(self, path: str, name: str, duration_ms: int,
                              wait: bool = True,
                              grace_s: float | None = None) -> dict:
        """Send one action frame and hold the queue until the robot finishes."""
        # The animation moves joints we never see and ends in a stance the
        # firmware picks, so our remembered values stop describing the robot.
        self._stale = True

        # Hold the queue for the whole animation, not just the write. A second
        # action arriving now waits its turn instead of being dropped, and a
        # state poll cannot interleave and spoil the progress stream.
        await self._command_lock.acquire()
        try:
            # Reset the completion signal INSIDE the critical section. Clearing
            # it before waiting for the lock would let this action inherit the
            # `set()` left behind by the one it queued behind, and it would then
            # report "progress-100" instantly without the robot ever moving.
            self._progress.clear()
            self._progress_seen = False
            self._last_progress = None
            self._saw_partial = False
            self._action_trace = []
            self._action_peak = None
            self._firmware_ms = None
            self._action = name
            self._action_started = time.monotonic()
            await self._send("action", path, _held=True)
            self.emit("action", {"name": name, "duration_ms": duration_ms,
                                 "path": path})

            if wait:
                completion = await self._await_action(name, duration_ms, grace_s)
                detail = self._completion_detail(completion)
                self._action = None
                self._command_lock.release()
            else:
                # Fire and forget, but keep holding the queue until the robot
                # genuinely finishes - otherwise "don't wait" would mean "let
                # the next action cut in", which is the bug being fixed here.
                self._spawn(self._finish_action(name, duration_ms, grace_s))
                detail = {"completion": "queued"}
        except BaseException:
            self._action = None
            self._command_lock.release()
            raise

        return {"action": name, "duration_ms": duration_ms, **detail}

    def _completion_detail(self, completion: str) -> dict:
        """Everything the wait learned, so callers need not re-derive it.

        `firmware_ms` is write->the accepted progress-100, and is `None` unless
        the robot actually reported the action running first - a completion
        without a start is not evidence of anything and must not become a
        timing. `trace` is every progress packet with its offset from the write,
        which is what makes a bad attribution visible in the artifact instead of
        having to be inferred afterwards.
        """
        elapsed = None
        if self._action_started is not None:
            elapsed = (time.monotonic() - self._action_started) * 1000.0
        return {
            "completion": completion,
            "elapsed_ms": round(elapsed, 1) if elapsed is not None else None,
            "firmware_ms": (round(self._firmware_ms, 1)
                            if self._firmware_ms is not None else None),
            "progress": self._action_peak,
            "saw_start": self._saw_partial,
            "trace": list(self._action_trace),
        }

    async def _finish_action(self, name: str, duration_ms: int,
                             grace_s: float | None = None):
        """Release the command queue once a fire-and-forget action completes."""
        try:
            completion = await self._await_action(name, duration_ms, grace_s)
            log.info("queued %r finished: %s", name, completion)
        except Exception:  # noqa: BLE001 - a background waiter must not leak
            log.exception("background completion for %r failed", name)
        finally:
            self._action = None
            self._command_lock.release()

    def _action_deadline_s(self, duration_ms: int, grace_s: float | None = None) -> float:
        """How long to wait for this action before calling it a timeout.

        A fixed cap truncates long dances: `Robot Talk` is declared at 81s and
        `Cyberpunk` at 58s, both well past the 10s floor. So the default waits
        for the declared clock plus slack, and `grace_s` lets a caller override
        the whole figure - useful for a benchmark that must not hang on a robot
        which has stopped reporting.
        """
        if grace_s is not None:
            return max(0.0, float(grace_s))
        return max(self.action_grace_s, duration_ms / 1000.0 + self.action_slack_s)

    async def _await_action(self, name: str, duration_ms: int,
                            grace_s: float | None = None) -> str:
        """Wait for a stunt to finish and report *how* we decided.

        Returns which signal ended the wait:

        - `progress-100` - the robot said it finished. This is the normal path.
        - `quiet` - progress stopped arriving for a while. A backstop.
        - `timeout` - nothing conclusive; the robot may still be animating.

        Callers that queue actions must treat `timeout` as "still busy". The
        robot runs one action at a time, and a second action sent while the first
        is animating is dropped without complaint - which is exactly how a punch
        went missing while testing, and the reason routines stop on `timeout`.
        """
        self._progress_seen = False
        self._last_progress = None
        deadline_s = self._action_deadline_s(duration_ms, grace_s)
        self._action_deadline = deadline_s
        deadline = time.monotonic() + deadline_s
        # Scale the silence backstop with the stunt: see ACTION_QUIET_FRACTION.
        quiet_s = max(self.action_quiet_s,
                      duration_ms / 1000.0 * ACTION_QUIET_FRACTION)

        try:
            while True:
                if self._progress.is_set():
                    return "progress-100"
                now = time.monotonic()
                # Only an action that never reported itself running can be
                # called finished by silence. Once a start packet has arrived,
                # the stream is the only authority: `Open Fire` is declared at
                # 1.4s but streamed 8..83% for 8.4s with ~1.1s gaps, and every
                # silence window short enough to be a backstop cut it off.
                if (not self._saw_partial and self._last_progress
                        and now - self._last_progress > quiet_s):
                    return "quiet"
                if now >= deadline:
                    log.warning(
                        "no completion packet for %s after %.0fs - it may still be "
                        "animating; do not queue another action yet", name, deadline_s,
                    )
                    return "timeout"
                await asyncio.sleep(0.05)
        finally:
            self._action_deadline = 0.0

    async def i_say(self, clip: int = 101, **_) -> dict:
        """Play one built-in clip by number, via the string-path command.

        The robot has no text-to-speech: `audio` takes a path to a clip that
        already exists on the robot, never words of your own. Nothing in the
        protocol uploads a file either, so generated speech has to reach the
        robot some other way - over USB-C, into the `02` folder - before it can
        be played. See `speak`.

        Only ids declared in robot.json are meaningful here, since robot.json
        is a build-time document and cannot know what the phone app has put on
        the robot since. Use `audio` to ask the robot itself, and `speak` to
        play your own files by path.
        """
        declared = spec.audio_clips()
        if declared and int(clip) not in declared:
            raise IntentError(
                f"clip {clip} is not declared in the spec; known ids are "
                f"{declared[0]}-{declared[10]} and {declared[11]}-{declared[-1]}. "
                f"To play your own audio, use speak(path=...) instead."
            )
        await self._send("audio", f"AppSysMS/{int(clip)}")
        return {"clip": int(clip), "group": spec.clip_name(clip)}

    async def i_speak(self, path: str, **_) -> dict:
        """Play an audio file by its on-device path, e.g. "02/story01".

        This is the route to the robot saying arbitrary words. The protocol has
        no text-to-speech and no upload command: `audio` takes the *path* of a
        file that already exists on the robot, never words of your own. So
        generated speech has to reach the robot some other way - in practice
        over USB-C - and then be named here.

        Two things learned from the robot's SD card rather than from
        robot.json, which only ever mentions AppSysMS:

        - The firmware appends the extension. The spec's `AppSysMS/101` is the
          file `AppSysMS/101.mp3`, so send `02/story01` for `02/story01.mp3`.
        - User audio belongs in the `02` folder. The card's own warning file
          says so, and marks AppSysMS and friends as do-not-touch.

        Structural checks only below: this string reaches the firmware, so keep
        it boring - relative, no `..`, no drive letters, no control characters.
        """
        cleaned = str(path).strip().replace("\\", "/")
        if not cleaned:
            raise IntentError("path is empty")
        if ".." in cleaned.split("/"):
            raise IntentError(f"path {path!r} contains '..'")
        if cleaned.startswith("/") or ":" in cleaned:
            raise IntentError(f"path {path!r} must be relative, like '02/story01'")
        if any(ord(ch) < 32 for ch in cleaned):
            raise IntentError(f"path {path!r} contains control characters")
        folder = cleaned.split("/", 1)[0]

        # Only warn, never block: the card's own guidance is advisory for us
        # because we are playing, not writing.
        advice = None
        if folder in spec.SD_PROTECTED:
            advice = (f"{folder} is marked do-not-modify on the SD card; add your "
                      f"own audio to {spec.USER_AUDIO_FOLDER}/ instead")
        elif folder != spec.USER_AUDIO_FOLDER:
            advice = (f"the card suggests {spec.USER_AUDIO_FOLDER}/ for "
                      f"user-programmed audio")

        stem = cleaned.rsplit("/", 1)[-1]
        naming = None
        if folder == spec.USER_AUDIO_FOLDER and not stem.isdigit():
            naming = (f"verified on hardware: {folder}/ does not resolve a lettered "
                      f"filename - 02/story01.mp3 is silent while 02/101.mp3 plays. "
                      f"Use a numeric id, e.g. 02/201")

        known = self._folders_seen
        await self._send("audio", cleaned)
        return {
            "path": cleaned,
            "expects_file": cleaned + spec.audio_extension(),
            "folder": folder,
            # Always None on real hardware: folderNames comes back empty, so the
            # robot never confirms anything. The card is the real inventory.
            "folder_confirmed": folder in known if known else None,
            "known_folders": sorted(known) if known else None,
            "naming_advice": naming,
            "advice": advice,
        }

    async def i_move(self, direction: str, ms: int = 1000, **_) -> dict:
        """Walk. Not a subtle motion: the robot drops into a locomotion stance.

        Observed on hardware - even a 500ms 'turn left' makes it bend its knees
        and lower into a running position. So every move is a posture change,
        which is why it marks the tracked pose stale and why it deserves the
        same suspicion as a stunt. There is no speed control in the protocol:
        duration is the only knob, and the spec's minimum is 500ms.
        """
        code = DIRECTIONS.get(str(direction).lower())
        if not code:
            raise IntentError(f"unknown direction {direction!r}; try {sorted(DIRECTIONS)}")
        ms = max(MOVE_MIN_MS, min(MOVE_MAX_MS, int(ms)))
        if ms < MOVE_USEFUL_MS:
            log.warning(
                "move of %dms is under the %dms that actually walks; the robot "
                "spends roughly the first %dms entering its locomotion stance",
                ms, MOVE_USEFUL_MS, MOVE_SETTLE_MS,
            )
        # Hold the queue for the whole walk: the robot is committed from the
        # first frame to the stop, and anything sent in between would arrive
        # mid-stride.
        async with self._command_lock:
            await self.bus.write(protocol.packet(code))
            # The animation moves joints we never see and ends in a stance the
            # firmware picks, so our remembered values stop describing the robot.
            self._stale = True
            self.emit("move", {"direction": direction, "ms": ms, "posture_change": True})
            await self._nap(ms / 1000)
            await self._send("stop", _held=True)
        return {"direction": direction, "ms": ms, "posture_change": True}

    async def i_stop(self, **_) -> dict:
        await self._send("stop")
        return {"stopped": True}

    def _require_fresh(self):
        """Refuse joint writes that would use out-of-date values.

        The struct is fixed-width, so a pose command re-sends *every* joint at
        our remembered values. After an action or move those no longer describe
        the hardware, and the command silently drags the body back to them.
        """
        if self._stale:
            raise IntentError(
                "the tracked pose is stale - an action or move has run since the "
                "last joint write, and this frame would re-send every joint at "
                "remembered values (observed: the robot leans). Call 'stand' "
                "first to reset to neutral, or pass assume_stale_ok=True."
            )

    async def i_glide(self, pose: dict | None = None, speed: int = 30,
                      max_step: int = 8, step_delay: float | None = None,
                      assume_stale_ok: bool = False, **_) -> dict:
        """Move to a pose over several frames instead of one big jump.

        `pose` and `stand` teleport all 17 joints in a single frame. The factory
        stunts do not: those 32-byte records on the SD card *are* the easing, and
        `Mechanical Beats` ramps through 114 keyframes where `Left Punch` uses 2.
        Stepping every joint at once is the largest mechanical shock this library
        can inflict on the servos, so this intent ramps instead.

        `max_step` is the biggest per-joint change allowed in one frame, in
        counts (one count = one degree on a 200-degree servo). The step count is
        derived from the largest delta so short moves stay short - a 10-degree
        head turn is one frame, not a full ramp.

        Added as a separate intent rather than changing `pose`: the single-frame
        behaviour is verified on hardware and routines may rely on it, so this
        is opt-in and the two can be compared.
        """
        self._require_fresh()
        limits = spec.joints()
        max_step = max(1, int(max_step))
        delay = DELAY if step_delay is None else max(0.0, float(step_delay))

        start = dict(self.joints)
        target = protocol.joint_struct(
            {**start, **protocol.clamp_pose(pose or {})}, speed=speed)

        deltas = {name: abs(target[name] - start.get(name, limits[name]["neutral"]))
                  for name in limits}
        biggest = max(deltas.values(), default=0)
        steps = max(1, -(-biggest // max_step))  # ceil
        moving = {n: d for n, d in deltas.items() if d}

        self.emit("glide", {"steps": steps, "max_delta": biggest,
                            "speed": speed, "moving": sorted(moving)})

        # One lock for the whole ramp: a glide is one logical command, and
        # letting a poll or an action interleave between frames would defeat it.
        async with self._command_lock:
            frame = target
            for step in range(1, steps + 1):
                frac = step / steps
                frame = dict(target)
                for name in moving:
                    origin = start.get(name, limits[name]["neutral"])
                    frame[name] = round(origin + (target[name] - origin) * frac)
                await self._send("jointMove", frame, _held=True)
                if step < steps:
                    await self._nap(delay)

        self.joints = frame
        return {
            "pose": protocol.clamp_pose(pose or {}),
            "speed": speed,
            "steps": steps,
            "max_delta": biggest,
            "moved_joints": sorted(moving),
        }

    async def i_pose(self, pose: dict | None = None, speed: int = 40,
                      assume_stale_ok: bool = False, **_) -> dict:
        """Absolute joint values, clamped to documented limits.

        Joints the caller did not mention hold their current position, so
        `pose({"head": 150})` moves the head and nothing else. Names that are
        not in the spec are dropped with a warning by clamp_pose.

        Raises if the tracked pose is stale (an action or move has run since the
        last write) and `assume_stale_ok` is not set. The struct is fixed-width,
        so this call re-sends *every* joint at our remembered values; if those
        are out of date it silently drags the body back to them. That was
        observed on hardware: a head-only command after a stunt made the robot
        lean right and forward. `stand` first, or pass `assume_stale_ok`.
        """
        if self._stale and not assume_stale_ok:
            self._require_fresh()
        wanted = protocol.clamp_pose(pose or {})
        merged = protocol.joint_struct({**self.joints, **wanted}, speed=speed)
        await self._send("jointMove", merged)
        self.joints = merged
        self.emit("pose", {"speed": speed, **wanted})
        await self._nap(DELAY)
        return {"pose": wanted, "speed": speed}

    async def i_delta(self, delta: dict, speed: int = 40, assume_stale_ok: bool = False, **_) -> dict:
        """Relative joint values - friendlier than absolute for scripting."""
        limits = spec.joints()
        moved = {}
        for key, value in (delta or {}).items():
            if key in limits:
                base = self.joints.get(key, limits[key]["neutral"])
                moved[key] = base + int(value)
        return await self.i_pose(moved, speed=speed, assume_stale_ok=assume_stale_ok)

    async def i_stand(self, speed: int = 40,
                      allow_leaning_neutral: bool = False, **_) -> dict:
        """Write the `robot.json` neutral frame - which is *not* neutral.

        Hardware finding (K1-XXXX, 2026-10-30): this frame leans the robot right
        and forward, pins `rightArm` at its documented minimum (34), and the
        robot then announced a stuck right shoulder. An action sent after this
        `jointMove` was also ignored by the firmware. It is therefore refused by
        default; the honest reset is to let a factory action return the robot to
        its own firmware stance.

        The mechanics are kept for a watched experiment only. Pass
        `allow_leaning_neutral=True` if you really mean it.

        This still *resets* the tracked pose rather than merging with it, which
        is why the code exists: `i_pose({})` re-sends whatever we last commanded
        and is not a reset at all.
        """
        if not allow_leaning_neutral:
            raise IntentError(
                "refusing to write robot.json's 'neutral': on hardware it leans "
                "right/forward, pins rightArm at its minimum (34), and the robot "
                "reported a stuck right shoulder. It is not a safe reset. Let a "
                "factory action return the robot to its own stance, or pass "
                "allow_leaning_neutral=True if you are watching and mean it."
            )
        neutral = protocol.joint_struct(speed=speed)
        await self._send("jointMove", neutral)
        self.joints = neutral
        self._stale = False
        self.emit("pose", {"speed": speed, "stand": True, "leaning": True})
        await self._nap(DELAY)
        return {"pose": "robot.json neutral", "speed": speed, "leaning": True}

    async def i_head(self, degrees: int, speed: int = 30, assume_stale_ok: bool = False, **_) -> dict:
        base = self.joints.get("head", spec.joints()["head"]["neutral"])
        return await self.i_pose({"head": base + int(degrees)}, speed=speed,
                                 assume_stale_ok=assume_stale_ok)

    async def i_readPose(self, timeout: float = 1.0, **_) -> dict:
        """Ask the robot where its joints are, and report honestly.

        Empirically the K1 does NOT answer `jointSync` (`e9`): no notification
        comes back, so the joint positions are genuinely unknowable over BLE.
        Rather than pass off the local cache as a readback - which would look
        like real data and quietly invite someone to trust it - this waits for
        an actual reply and says so when there isn't one.
        """
        before = self._reply_count
        try:
            await self._send("jointSync", protocol.joint_struct())
        except Exception as exc:
            return {"joints": None, "ok": False,
                    "error": f"jointSync rejected by the transport: {exc}"}

        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            if self._reply_count > before and self.state is not None:
                return {"joints": dict(self.state), "ok": True, "verified": True}
            await asyncio.sleep(0.05)

        return {
            "joints": None,
            "ok": False,
            "verified": False,
            "error": (
                "no jointSync reply arrived. Verified on real hardware (K1-XXXX): "
                "the robot does not implement jointSync, so true joint positions "
                "cannot be read over BLE. Track commanded positions locally in "
                "RobotCore.joints instead."
            ),
        }

    async def i_shutdown(self, **_) -> dict:
        await self._send("shutdown")
        self.emit("shutdown", {})
        return {"shutdown": True}

    async def i_routine(self, steps: Iterable, gap: float = 0.35, **_) -> dict:
        played: list[str] = []
        for i, step in enumerate(steps, 1):
            if isinstance(step, str):
                step = {"action": step}
            if not isinstance(step, dict):
                raise IntentError(f"step {i}: expected a string or object, got {type(step).__name__}")
            if "wait" in step:
                await self._nap(float(step["wait"]))
                continue
            key = next((k for k in self.STEP_INTENTS if k in step), None)
            if key is None:
                raise IntentError(
                    f"step {i}: no known key in {sorted(step)}; try {list(self.STEP_INTENTS)}"
                )
            method, params = self.STEP_INTENTS[key](step)
            result = await self.execute(method, params)
            # Each step goes through the command queue, so a step can never
            # interleave with the one before it even if a client fires the whole
            # routine concurrently. A `timeout` completion means the robot never
            # confirmed it finished; the next step would be dropped, so stop.
            if isinstance(result, dict) and result.get("completion") == "timeout":
                raise IntentError(
                    f"step {i} ({step.get('action')!r}) never reported completion. "
                    f"The robot may still be animating it, and a command sent now "
                    f"would be dropped, so the routine stops here. Re-run once the "
                    f"robot is idle."
                )
            played.append(method)
            await self._nap(gap)
        return {"steps": len(played), "played": played}

    # -- dispatch ----------------------------------------------------------
    HANDLERS = {
        "state": "i_state",
        "info": "i_info",
        "volume": "i_volume",
        "autoStand": "i_autoStand",
        "autoOff": "i_autoOff",
        "action": "i_action",
        "actionPath": "i_action_path",
        "say": "i_say",
        "speak": "i_speak",
        "folders": "i_folders",
        "clipInfo": "i_clip_info",
        "list": "i_list",
        "audio": "i_audio",
        "move": "i_move",
        "stop": "i_stop",
        "pose": "i_pose",
        "glide": "i_glide",
        "delta": "i_delta",
        "stand": "i_stand",
        "head": "i_head",
        "readPose": "i_readPose",
        "shutdown": "i_shutdown",
        "routine": "i_routine",
    }

    async def execute(self, method: str, params: dict | None = None) -> Any:
        handler = getattr(self, self.HANDLERS.get(method, ""), None)
        if handler is None:
            raise IntentError(f"unknown intent {method!r}; try {sorted(self.HANDLERS)}")
        return await handler(**(params or {}))

    async def run(self, method: str, params: dict | None = None) -> Any:
        """execute() with the error mapped to a structured result."""
        try:
            return {"ok": True, "result": await self.execute(method, params)}
        except IntentError as exc:
            return {"ok": False, "error": str(exc)}
        except Exception as exc:  # noqa: BLE001 - surface, never crash the core
            log.exception("intent %s failed", method)
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


RobotCore.STEP_KEYS = STEP_KEYS
RobotCore.STEP_INTENTS = STEP_INTENTS


def make_bus(kind: str = "sim", name_contains: str = "K1") -> Bus:
    kind = kind.lower()
    if kind == "sim":
        return SimBus()
    if kind == "ble":
        return BleBus(name_contains)
    raise ValueError(f"unknown bus {kind!r}; try sim or ble")