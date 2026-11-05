"""Transports ("buses") for the K1.

The core never learns how it is talking to the robot. That is the whole point:
the same routine runs against the simulator with nothing plugged in, and against
the real robot over BLE, with no code change. Modelled on Microduck's split
between `robotd` (owns the robot) and the transports around it.

    SimBus   - no hardware. Validates frames, tracks state, records a session.
    BleBus   - the real K1 over Bluetooth LE.

Both expose `write(frame)` and push inbound packets to `on_packet`.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from abc import ABC, abstractmethod
from pathlib import Path

from . import protocol, spec

log = logging.getLogger("k1.bus")


class NotFound(RuntimeError):
    pass


class Bus(ABC):
    """Anything that can carry K1 frames."""

    name: str = "bus"

    def __init__(self):
        self.on_packet = None
        # Every outbound frame, timestamped. Lives on the base class so BLE is
        # as inspectable as the simulator - reverse-engineering a protocol you
        # can only watch through one transport is guesswork.
        self.session: list[dict] = []

    def _record(self, frame: bytes):
        self.session.append({"t": round(time.monotonic(), 4), "hex": frame.hex()})

    @abstractmethod
    async def open(self) -> str:
        """Connect / power up. Returns a human label for logs."""

    @abstractmethod
    async def close(self):
        pass

    @abstractmethod
    async def write(self, frame: bytes):
        """Send one frame to the robot."""

    @property
    def connected(self) -> bool:
        return True

    def _emit(self, frame: bytes):
        if self.on_packet:
            self.on_packet(protocol.parse(frame))


# Read-only identification queries, with the values real hardware (K1-XXXX)
# actually reported, so scripts that branch on them behave the same in sim and
# on the robot.
INFO_REPLIES = (
    ("kind", "K1"),
    ("version", "VER:3.04L"),
    ("date", "SH2022-08-15"),
)

# List-type queries the simulator answers. These are *computed from the spec*
# rather than hardcoded, so the sim can never claim a clip exists that
# robot.json does not declare. Real hardware may also list clips the phone app
# downloaded, which is precisely what makes the on-device list worth asking for.
LIST_REPLIES = {
    "audioNames": tuple(f"AppSysMS/{c}" for c in spec.audio_clips()),
    "folderNames": tuple(spec.audio_folders()),
    "actionNames": tuple(spec.actions()),
}

# Fault injection: set False to make list replies stop without sending `done`,
# which is how a robot that is busy or mid-update would behave. Tests use it to
# prove a truncated list is never reported as a complete one.
LIST_TERMINATES = True


# ---------------------------------------------------------------------------
# Simulator
# ---------------------------------------------------------------------------
class SimBus(Bus):
    """A fake robot.

    It rejects malformed frames (bad checksum, unknown type) exactly where the
    firmware would, tracks battery/volume/toggles, and answers state queries.
    Stunts and dances are timed for real, so routine pacing is exercised.
    """

    name = "sim"

    def __init__(self, battery: int = 100, volume: int = 100, speed: float = 1.0):
        super().__init__()
        self.battery = battery
        self.volume = volume
        self.toggles = {"autoStand": 1, "autoTurn": 0, "autoPose": 0, "autoOff": 0}
        self.joints: dict[str, int] = protocol.joint_struct()
        self.speed_factor = speed  # >1 runs actions faster, for tests
        self.rejected: list[str] = []
        self.shutting_down = False
        self._action_task: asyncio.Task | None = None
        self._tasks: set[asyncio.Task] = set()

    async def open(self) -> str:
        return "K1 (simulated)"

    async def close(self):
        for task in list(self._tasks):
            task.cancel()

    # -- outbound: robot validating what the core sent ---------------------
    async def write(self, frame: bytes):
        pkt = protocol.parse(frame)
        if pkt is None or not pkt.valid:
            self.rejected.append(frame.hex())
            log.warning("sim rejected malformed frame %s", frame.hex())
            return

        self._record(frame)
        code = pkt.type

        if code == spec.code("state"):
            self._emit(self._state_frame())
            return

        for name, text in INFO_REPLIES:
            if code == spec.code(name):
                self._emit(protocol.packet(code, text))
                return

        # List queries: a stream of items of the queried type, then `done`.
        for name, items in LIST_REPLIES.items():
            if code == spec.code(name):
                for item in items:
                    self._emit(protocol.packet(code, item))
                if LIST_TERMINATES:
                    self._emit(protocol.packet(spec.code("done")))
                return

        if code == spec.code("volume"):
            self.volume = max(0, min(140, pkt.value() if isinstance(pkt.value(), int) else self.volume))
            return

        for toggle in ("autoStand", "autoTurn", "autoPose", "autoOff"):
            if code == spec.code(toggle):
                self.toggles[toggle] = 1 if pkt.value() else 0
                return

        if code == spec.code("jointMove") and pkt.struct:
            self.joints = {k: v for k, v in pkt.struct.items()}
            return

        if code == spec.code("action"):
            self._start_action(pkt.text())
            return

        if code == spec.code("stop") and self._action_task:
            # The robot stops mid-animation and never sends its 100% packet.
            # Real behaviour: the caller waits out the timeout and logs
            # "assuming done", so the sim reproduces that rather than papering
            # over it with a completion the hardware would not send.
            self._action_task.cancel()
            self._action_task = None
            return

        if code == spec.code("shutdown"):
            self.shutting_down = True
            return

        if code == spec.code("jointSync"):
            # Deliberately silent. Verified against real hardware (K1-XXXX): the
            # robot accepts the frame and never replies. Answering here would
            # make the simulator more capable than the real thing and hide the
            # fact that joint positions are simply not readable over BLE.
            log.debug("sim: ignoring jointSync (real hardware does not answer it)")
            return

        # directions, stop, audio and friends: nothing to model

    def _state_frame(self) -> bytes:
        payload = bytes(
            [
                0,
                self.battery,
                self.volume & 0xFF,
                0,
                self.toggles["autoStand"],
                self.toggles["autoTurn"],
                self.toggles["autoPose"],
                self.toggles["autoOff"],
            ]
        )
        body = bytes([len(payload) + 2, int(spec.code("state"), 16)]) + payload
        return spec.header() + body + bytes([protocol.checksum(body)])

    def _start_action(self, path: str):
        """Kick off an animation, cancelling any still running."""
        if self._action_task:
            self._action_task.cancel()
        self._action_task = self._spawn(self._run_action(path))

    async def _run_action(self, path: str):
        """Emit progress like the firmware does, then completion.

        Observed on real hardware (K1-XXXX) when driven one command at a time:
        the robot reports 50 then 100 for `Left Punch`, and streams finer steps
        for longer stunts. The sim emits the coarse pair so timing code cannot
        depend on granularity the firmware does not promise.
        """
        name = path.rsplit("/", 1)[-1]
        try:
            duration = spec.duration_ms(name) / 1000 / self.speed_factor
        except KeyError:
            # A card-only stunt robot.json never names, so there is no clock for
            # it. Time it at the same placeholder the core uses rather than
            # hanging: the point of the sim is to catch malformed frames and
            # queue mistakes, not to promise a real duration.
            log.debug("sim: no spec duration for %r, using placeholder", path)
            duration = spec.UNKNOWN_ACTION_MS / 1000 / self.speed_factor
        started = time.monotonic()
        for pct in (50, 100):
            delay = (started + duration * (pct / 100)) - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
            self._emit(self._progress_frame(pct))

    def _progress_frame(self, pct: int) -> bytes:
        body = bytes([3, int(spec.code("action"), 16), pct])
        return spec.header() + body + bytes([protocol.checksum(body)])

    def _spawn(self, coro):
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    # -- session capture ---------------------------------------------------
    def save(self, path: str | Path):
        Path(path).write_text(
            json.dumps({"battery": self.battery, "frames": self.session}, indent=1),
            encoding="utf-8",
        )
        return path

    def hexlog(self) -> list[str]:
        return [entry["hex"] for entry in self.session]


# ---------------------------------------------------------------------------
# Bluetooth LE
# ---------------------------------------------------------------------------
class BleBus(Bus):
    """The real K1. Bluetooth LE only - the robot has no Wi-Fi."""

    name = "ble"

    def __init__(self, name_contains: str = "K1", scan_timeout: float = 12.0):
        super().__init__()
        self.name_contains = name_contains
        self.scan_timeout = scan_timeout
        self._client = None
        self._name: str | None = None

    async def scan(self):
        from bleak import BleakScanner

        found = []
        devices = await BleakScanner.discover(timeout=self.scan_timeout, return_adv=True)
        for address, (device, adv) in devices.items():
            name = device.name or adv.local_name
            if name and self.name_contains.lower() in name.lower():
                found.append((address, name, adv.rssi))
        found.sort(key=lambda row: -(row[2] or -999))
        return found

    async def find(self) -> tuple[str, str]:
        found = await self.scan()
        if not found:
            raise NotFound(
                f"No BLE device advertising a name containing {self.name_contains!r}.\n"
                "  - is the K1 powered on?\n"
                "  - close the K One app on your phone (BLE allows only one connection)"
            )
        address, name, _ = found[0]
        return address, name

    async def open(self) -> str:
        from bleak import BleakClient

        address, name = await self.find()
        log.info("connecting to %s (%s)", name, address)
        self._client = BleakClient(address)
        await self._client.connect()
        self._name = name
        try:
            await self._client.start_notify(spec.characteristic_uuid(), self._notify)
        except Exception as exc:  # some firmware exposes no notify characteristic
            log.warning("could not subscribe to notifications: %s", exc)
        return name

    async def close(self):
        if self._client is None:
            return
        try:
            await self._client.disconnect()
        finally:
            self._client = None

    def _notify(self, _sender, data: bytearray):
        pkt = protocol.parse(bytes(data))
        if pkt is None:
            return
        if not pkt.valid:
            log.warning("bad checksum on %s", pkt.type)
        if self.on_packet:
            self.on_packet(pkt)

    @property
    def connected(self) -> bool:
        return self._client is not None and self._client.is_connected

    @property
    def label(self) -> str:
        return self._name or "ble"

    async def write(self, frame: bytes):
        if not self.connected:
            raise RuntimeError("not connected")
        self._record(frame)
        await self._client.write_gatt_char(spec.characteristic_uuid(), frame, response=False)


async def scan_once(name_contains: str = "K1", timeout: float = 12.0):
    return await BleBus(name_contains, timeout).scan()