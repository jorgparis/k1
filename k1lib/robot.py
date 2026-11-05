"""Friendly facade over RobotCore.

Keeps the original ergonomic API (`async with K1() as k1: await k1.action(...)`)
while the core owns all the logic and the bus is swappable. Prefer this for
scripts; use RobotCore directly when you want events or a custom bus.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Callable, Iterable

from . import protocol, spec  # noqa: F401  (re-exported for callers)
from .bus import BleBus, Bus, SimBus  # noqa: F401  (re-exported for callers)
from .core import DIRECTIONS, MOVE_MAX_MS, MOVE_MIN_MS, IntentError, RobotCore, make_bus  # noqa: F401

log = logging.getLogger("k1")


class K1:
    """The K1, over whatever bus you hand it."""

    def __init__(self, bus: str | Bus = "ble", name_contains: str = "K1",
                 auto_stand: bool = True):
        self.core = RobotCore(bus if isinstance(bus, Bus) else make_bus(bus, name_contains))
        self._auto_stand = auto_stand

    # -- plumbing ----------------------------------------------------------
    async def connect(self) -> str:
        label = await self.core.start()
        if self._auto_stand:
            await self.core.i_autoStand(True)
        return label

    async def close(self):
        # Delegate rather than aliasing the function. `close = RobotCore.stop`
        # looks tidy but binds `self` to the K1 facade, which only worked while
        # RobotCore.stop touched nothing but `.bus` - and `.bus` happens to proxy
        # through to the core. Any core-only attribute silently breaks it.
        await self.core.stop()

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, *_exc):
        await self.close()

    @property
    def simulated(self) -> bool:
        return self.core.simulated

    @property
    def bus(self) -> Bus:
        return self.core.bus

    def on(self, callback: Callable[[str, dict], None]):
        self.core.on(callback)

    # -- info --------------------------------------------------------------
    async def state(self) -> dict:
        return await self.core.i_state()

    async def info(self) -> dict:
        """Model, firmware version and build date. Moves nothing."""
        return await self.core.i_info()

    @property
    def last_state(self) -> dict | None:
        return self.core.state

    @property
    def joints(self) -> dict:
        return dict(self.core.joints)

    # -- config ------------------------------------------------------------
    async def volume(self, level: int):
        return await self.core.i_volume(level)

    async def auto_stand(self, on: bool = True):
        return await self.core.i_autoStand(on)

    async def auto_off(self, on: bool = True):
        return await self.core.i_autoOff(on)

    async def shutdown(self):
        return await self.core.i_shutdown()

    # -- stunts & audio ----------------------------------------------------
    async def action(self, name: str, wait: bool = True, allow_fall_risk: bool = False):
        """Play a canned factory stunt. All factory actions are safe to play."""
        return await self.core.i_action(name, wait=wait, allow_fall_risk=allow_fall_risk)

    async def say(self, clip: int = 101):
        return await self.core.i_say(clip)

    # -- locomotion --------------------------------------------------------
    async def move(self, direction: str, ms: int = 1000):
        return await self.core.i_move(direction, ms)

    async def stop(self):
        return await self.core.i_stop()

    # -- poses -------------------------------------------------------------
    async def pose(self, poses: dict | None = None, speed: int = 40,
                 assume_stale_ok: bool = False):
        return await self.core.i_pose(poses, speed=speed, assume_stale_ok=assume_stale_ok)

    async def glide(self, poses: dict | None = None, speed: int = 30,
                    max_step: int = 8, step_delay: float | None = None,
                    assume_stale_ok: bool = False):
        """Ease to a pose over several frames instead of one jump.

        Gentler on the servos than `pose`: no joint moves more than `max_step`
        counts in a frame. Opt-in, so the verified single-frame `pose` is
        untouched.
        """
        return await self.core.i_glide(poses, speed=speed, max_step=max_step,
                                       step_delay=step_delay,
                                       assume_stale_ok=assume_stale_ok)

    async def pose_delta(self, deltas: dict, speed: int = 40, assume_stale_ok: bool = False):
        return await self.core.i_delta(deltas, speed=speed, assume_stale_ok=assume_stale_ok)

    async def stand(self, speed: int = 40, allow_leaning_neutral: bool = False):
        """Write robot.json's neutral frame.

        Refused by default: on hardware it leans the robot and reported a stuck
        right shoulder. Prefer a factory action to return it to its own stance.
        """
        return await self.core.i_stand(
            speed, allow_leaning_neutral=allow_leaning_neutral)

    async def head(self, degrees: int, speed: int = 30, assume_stale_ok: bool = False):
        return await self.core.i_head(degrees, speed, assume_stale_ok=assume_stale_ok)

    async def read_pose(self) -> dict:
        return await self.core.i_readPose()

    # -- routines ----------------------------------------------------------
    async def routine(self, steps: Iterable, gap: float = 0.35):
        return await self.core.i_routine(steps, gap=gap)

    async def routine_file(self, path: str | Path, gap: float = 0.35):
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        steps = data["steps"] if isinstance(data, dict) else data
        return await self.routine(steps, gap=gap)

    async def run(self, method: str, params: dict | None = None) -> Any:
        """Structured-result form of execute(): never raises for a bad intent."""
        return await self.core.run(method, params)