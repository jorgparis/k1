"""Single source of truth: reads the K1 spec straight out of robosen-js.

We deliberately do not hand-copy the joint table or stunt list. Reading
robot.json means this library can never drift from the SDK it drives.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

# robot.json ships inside the installed robosen-js package.
_SEARCH = Path(__file__).resolve().parent.parent / "node_modules" / "robosen-js" / "src" / "K1" / "robot.json"


@lru_cache(maxsize=1)
def load() -> dict:
    if not _SEARCH.exists():
        raise FileNotFoundError(
            f"K1 spec not found at {_SEARCH}\n"
            "Run: npm install robosen-js   (in this directory)"
        )
    with _SEARCH.open(encoding="utf-8") as fh:
        return json.load(fh)


def header() -> bytes:
    """Two-byte packet header, ffff."""
    return bytes.fromhex(load()["spec"]["header"])


def service_uuid() -> str:
    return _expand(load()["spec"]["serviceUuid"])


def characteristic_uuid() -> str:
    return _expand(load()["spec"]["characteristicsUuid"])


def _expand(short: str) -> str:
    """0000ffe0 -> 0000ffe0-0000-1000-8000-00805f9b34fb (Bluetooth base UUID)."""
    short = short.lower().removeprefix("0000")
    return f"0000{short}-0000-1000-8000-00805f9b34fb"


# --- joints -----------------------------------------------------------------
# indices 0..16 are real joints; 17..23 are fixed filler; 24 is speed.
def joints() -> dict[str, dict]:
    out = {}
    for key, entry in load()["joint"].items():
        if entry.get("index") is None or not entry.get("joint"):
            continue
        out[key] = {
            "index": entry["index"],
            "neutral": entry["value"],
            "min": entry["min"],
            "max": entry["max"],
        }
    return dict(sorted(out.items(), key=lambda kv: kv[1]["index"]))


def fixed_joints() -> dict[str, int]:
    """Indices that are not real joints but must be sent at a constant value."""
    out = {}
    for key, entry in load()["joint"].items():
        idx = entry.get("index")
        if idx is None or entry.get("joint"):
            continue
        out[key] = entry["value"]
    return out


def joint_size() -> int:
    """Total bytes in a joint struct (max index + 1)."""
    return max(e["index"] for e in load()["joint"].values() if e.get("index") is not None) + 1


def speed() -> dict:
    entry = load()["joint"]["speed"]
    return {"index": entry["index"], "neutral": entry["value"], "min": entry["min"], "max": entry["max"]}


# --- type codes -------------------------------------------------------------
def types() -> dict:
    return load()["type"]


def type_by_code(code: str) -> tuple[str, dict] | None:
    """robot.json keys its type table by name ('jointMove'); the wire uses the
    hex code ('e8'). This maps code -> (name, config)."""
    code = code.lower()
    for name, entry in types().items():
        if entry.get("code") == code:
            return name, entry
    return None


def code(name: str) -> str:
    """'moveNorth' -> '01'"""
    entry = load()["type"].get(name)
    if not entry:
        raise KeyError(f"unknown command type {name!r}")
    return entry["code"]


# --- stunts / dances --------------------------------------------------------
def actions() -> dict[str, dict]:
    """name -> {folder, duration_ms}. Action = music/dance, ProAction = stunt."""
    out: dict[str, dict] = {}
    for group, folder in (("Action", "Action"), ("ProAction", "ProAction")):
        for name, entry in load()["command"].get(group, {}).items():
            if name == "group" or not isinstance(entry, dict):
                continue
            out[name] = {
                "folder": folder,
                "path": f"{folder}/{name}",
                "duration_ms": entry.get("duration", 0),
                "description": entry.get("description", ""),
            }
    return out


def duration_ms(action: str) -> int:
    entry = actions().get(action)
    if not entry:
        raise KeyError(f"unknown action {action!r}")
    return entry["duration_ms"]


# What to wait for when the spec has no clock for an action we sent by path.
# Only used so completion polling has a deadline; the real duration is unknown.
# The simulator uses the same figure, so a routine that runs clean in `--sim`
# does not pass there for a reason that would fail on hardware.
UNKNOWN_ACTION_MS = 2000


# ---------------------------------------------------------------------------
# Fall safety
# ---------------------------------------------------------------------------
# The K1 is a 17-servo biped that cannot catch itself. These canned routines
# lower its centre of mass, invert it, or move it onto one limb, and the robot
# has no way to recover from a fall mid-routine. They are blocked by default and
# require an explicit opt-in, so "no falling" is enforced by the library rather
# than by whoever is holding the remote.
#
# Matching is on whole words so "Lean Back" is caught by `lean` while innocent
# names are not.
_FALL_RISK_WORDS = (
    "lean", "crouch", "kneel", "lay", "lie", "play dead", "handstand",
    "flip", "somersault", "roll", "squat", "push up", "mountain running",
    "jump", "hop", "cartwheel", "sit", "bow", "propose", "exercise",
    "bend", "seat", "sneak", "wiggle", "rocking", "boogaloo",
    # Seen on the SD card but absent from robot.json, so they would otherwise
    # never be classified. Kicks throw the balance out; the drum stunt leans.
    "kick", "drum",
)
# Deliberately NOT "stand": it matched exactly one spec action, Handstand, which
# "handstand" above already catches - and it misclassified `Resume Standing`,
# the one stunt that helps the robot get back up. Verified against every name we
# know of (68 spec actions + 130 card scripts); "lie", "somersault", "jump",
# "hop", "cartwheel" and "sit" match nothing known and are kept only as cheap
# insurance against content the app adds later.


def classify_fall_risk(name: str) -> str | None:
    """The fall-risk word matched by *any* name, or None.

    This is the check that matters. `fall_risk()` can only classify names that
    robot.json happens to list, which left a hole: `action_path` can reach any
    stunt on the SD card, and the card holds stunts the spec has never heard of
    - `front flip`, `back flip`, `left kick`, `play drum` among them. Keying the
    guard off a precomputed dict let every one of those through.

    So classify the string we were actually given.
    """
    lowered = name.lower()
    for word in _FALL_RISK_WORDS:
        if word in lowered:
            return word
    return None


@lru_cache(maxsize=1)
def fall_risk() -> dict[str, str]:
    """Spec-listed actions that risk toppling the robot -> the word matched.

    Enumerated from the live spec so a new stunt added to robot.json is
    classified automatically instead of silently defaulting to "safe". For a
    name the spec does not list, use `classify_fall_risk`.
    """
    risky: dict[str, str] = {}
    for name in actions():
        word = classify_fall_risk(name)
        if word:
            risky[name] = word
    return risky


def is_fall_risk(action: str) -> bool:
    return action in fall_risk() or classify_fall_risk(action) is not None


def audio_folders() -> list[str]:
    """Audio folders declared in robot.json, e.g. AppSysMS."""
    return sorted(load().get("audio", {}))


# ---------------------------------------------------------------------------
# Factory actions are safe
# ---------------------------------------------------------------------------
# Every action in robot.json - and every .sh script on the SD card - is a
# vendor-authored trajectory, written and factory-tested against this robot's own
# balance controller. They are safe to play. That is the rule, and it is not
# conditional on anything we have observed.
#
# The keyword classifier below is therefore *informational only*. It is a guess
# about posture from an English name, and it is wrong in both directions: it
# flagged `Push Ups` and `Left Kick`, which perform cleanly, while `Push Ups` is
# in fact the floor recovery - blocked by the very words that make it useful.
# `fall_risk` is reported on every result so the guess is visible; it is not a
# veto. Use `strict=True` to opt back into vetoing on the guess.
#
# Verified by observation, and recorded here as examples rather than as an
# allow-list: the list is not the set of permitted actions, it is the set of
# actions somebody has actually watched. Everything else is equally factory.
VERIFIED_SAFE: dict[str, str] = {
    "Left Punch": "punched cleanly, upright (multiple times, including queued)",
    "Right Punch": "punched cleanly, upright (queued behind another action)",
    "Push Ups": "from lying horizontally: performed push-ups and ended standing - "
               "the only verified floor recovery",
    "Left Kick": "kicked cleanly, stayed upright",
}


def verified_safe() -> dict[str, str]:
    """Examples of factory actions watched on hardware -> the observation.

    Not an allow-list: every factory action is safe. This is a record of what
    has been seen, useful for provenance, not a gate.
    """
    return {n: note for n, note in VERIFIED_SAFE.items() if n in actions()}


# ---------------------------------------------------------------------------
# SD card layout
# ---------------------------------------------------------------------------
# NOT derivable from robot.json - read off the robot's own SD card over USB-C
# (Windows exposes it as a removable FAT volume) and cross-checked against the
# card's own "Warning instructions.txt". robot.json only ever mentions
# AppSysMS, so this is recorded knowledge, not computed knowledge.
#
# The card says, in translation: do not delete or modify SHR0, SysCF, SysOS,
# SysMS, WarnSysMS or AppSysMS; do not delete the Action or SpeAction folders;
# and user-programmed audio belongs in `02`, or a new numerically named folder.
USER_AUDIO_FOLDER = "02"

SD_PROTECTED = ("SHR0", "SysCF", "SysOS", "SysMS", "WarnSysMS", "AppSysMS")
SD_ACTIONS = ("Action", "ProAction", "SpeAction")
SD_SYSTEM_AUDIO = ("AppProMS", "00", "01", "02", "11")


def audio_extension() -> str:
    """Extension the firmware appends to an audio path.

    The spec's example is `AppSysMS/101` and the file on the card is
    `AppSysMS/101.mp3`, so the extension is implicit in the path we send.
    Confirmed on hardware: sending `02/101` plays `02/101.mp3`.
    """
    return ".mp3"


# ---------------------------------------------------------------------------
# Audio naming, established by experiment on real hardware
# ---------------------------------------------------------------------------
# Three probes, each changing one variable, all confirmed by ear:
#
#   AppSysMS/101      numeric   -> plays
#   02/101            numeric   -> plays    (the sanctioned user folder works)
#   02/story01        lettered  -> SILENT
#   AppProMS/Delight  lettered  -> plays
#
# So letters are not the problem - `02/` simply does not resolve a lettered
# filename, while system folders do. Numeric ids work everywhere. Until someone
# explains the mechanism, the rule to program against is: numeric names in 02/.
AUDIO_USER_FOLDER_NEEDS_NUMERIC = True

# `folderNames` and `audioNames` are real, working list queries - the streams
# arrive and terminate with `done` - but on real hardware they return nothing at
# all. So the robot cannot tell us what audio it holds, and `folder_confirmed`
# in `speak` can never be anything but None in practice. The SD card is the only
# reliable inventory.
AUDIO_LISTING_IS_EMPTY_ON_HARDWARE = True


def audio_clips() -> list[int]:
    """Every audio clip id the spec declares, expanded from its ranges.

    robot.json describes audio as folders of ranges rather than a flat list:

        AppSysMS.program  101-111      AppSysMS.joint  201-216

    so the ids are computed here rather than hand-copied. Clips the phone app
    has downloaded since are *not* in this list - only `audioNames` can reveal
    those, because the spec is a build-time document.
    """
    out: list[int] = []
    for folder, groups in load().get("audio", {}).items():
        for group, cfg in (groups or {}).items():
            rng = (cfg or {}).get("range", {})
            if "start" in rng and "end" in rng:
                out.extend(range(int(rng["start"]), int(rng["end"]) + 1))
    return sorted(out)


def clip_name(clip: int) -> str:
    """Which declared range a clip id falls in, for readable errors."""
    clip = int(clip)
    for folder, groups in load().get("audio", {}).items():
        for group, cfg in (groups or {}).items():
            rng = (cfg or {}).get("range", {})
            if rng.get("start", 0) <= clip <= rng.get("end", -1):
                return f"{folder}/{group}"
    return "unknown"


def safe_actions() -> list[str]:
    """Actions that keep the robot on its feet, shortest first."""
    risky = fall_risk()
    rows = [(n, e) for n, e in actions().items() if n not in risky]
    rows.sort(key=lambda kv: kv[1]["duration_ms"])
    return [n for n, _ in rows]