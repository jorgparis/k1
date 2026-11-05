"""Read-only inspection of the robot's SD card.

The card is a plain FAT volume that Windows exposes as a removable drive when
the robot is plugged in over USB-C. Everything here only ever opens files for
reading. Nothing in this module writes, moves or renames anything, by design:
the card's own `Warning instructions.txt` marks most of it do-not-touch, and a
tool that can corrupt a robot's stunt library is not worth the convenience.

Nothing here is derivable from robot.json. The spec is a build-time document: it
never mentions `SpeAction`, and it lists 68 actions where the card holds 130
stunt scripts.
"""
from __future__ import annotations

import struct
from pathlib import Path

from . import spec

RECORD_SIZE = 32
TERMINATORS = {b"\xfc\xfc", b"\xfb\xfb"}


class NotACard(Exception):
    pass


def looks_like_a_card(root: str | Path) -> bool:
    root = Path(root)
    return (root / "Warning instructions.txt").exists() and (root / "AppSysMS").is_dir()


def _stunt_dirs(root: Path) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for folder in ("Action", "ProAction", "SpeAction"):
        d = root / folder
        if d.is_dir():
            out[folder] = sorted(p.stem for p in d.glob("*.sh"))
    return out


def stunt_paths(root: str | Path = "D:/") -> list[dict]:
    """Every `.sh` on the card, as `{folder, name, path}`.

    Read-only and deliberately dumb: it lists what is there so a caller can ask
    the robot to play each one by path. `robot.json` never names `SpeAction`, so
    this is the only way to reach those 66 scripts.
    """
    root = Path(root)
    if not looks_like_a_card(root):
        raise NotACard(
            f"{root} does not look like a K1 card: expected both "
            f"'Warning instructions.txt' and an 'AppSysMS' folder. The card only "
            f"mounts while the robot is plugged in over USB-C and powered on."
        )
    return [
        {"folder": folder, "name": name, "path": f"{folder}/{name}"}
        for folder, names in _stunt_dirs(root).items()
        for name in names
    ]


def terminator(path: Path) -> str | None:
    """The two-byte trailer every stunt file ends with, as hex.

    Two variants exist (`fcfc` and `fbfb`). It is tempting to read that as a
    risk flag - it is not. Checked against all 130 files, the split does not
    track fall risk: 10 fall-risk files end `fbfb` but 28 end `fcfc`, including
    every long dance. Recorded because it is a real structural fact, not because
    it means anything yet.
    """
    data = path.read_bytes()
    if len(data) < RECORD_SIZE or len(data) % RECORD_SIZE:
        return None
    tail = data[-2:]
    return tail.hex() if tail in TERMINATORS else None


def inventory(root: str | Path = "D:/") -> dict:
    """Everything worth knowing about a mounted card, without touching it."""
    root = Path(root)
    if not looks_like_a_card(root):
        raise NotACard(
            f"{root} does not look like a K1 card: expected both "
            f"'Warning instructions.txt' and an 'AppSysMS' folder. The card only "
            f"mounts while the robot is plugged in over USB-C and powered on."
        )

    stunts = _stunt_dirs(root)
    known = {name.lower() for name in spec.actions()}

    unknown: list[dict] = []
    duplicates: dict[str, list[str]] = {}
    for folder, names in stunts.items():
        for name in names:
            key = name.lower()
            if key not in known:
                unknown.append({"folder": folder, "name": name,
                                "path": f"{folder}/{name}"})
            duplicates.setdefault(key, []).append(folder)

    variants = {k: v for k, v in duplicates.items() if len(v) > 1}

    audio = {}
    for folder in (*spec.SD_SYSTEM_AUDIO, "AppSysMS"):
        d = root / folder
        if d.is_dir():
            audio[folder] = sorted(p.name for p in d.glob("*.mp3"))

    return {
        "root": str(root),
        "user_audio_folder": spec.USER_AUDIO_FOLDER,
        "protected": list(spec.SD_PROTECTED),
        "stunt_files": sum(len(v) for v in stunts.values()),
        "stunts_by_folder": {k: len(v) for k, v in stunts.items()},
        "spec_actions": len(spec.actions()),
        "unknown_to_spec": unknown,
        "unknown_count": len(unknown),
        "two_folder_variants": len(variants),
        "terminators": _terminator_census(root, stunts),
        "audio_counts": {k: len(v) for k, v in audio.items()},
    }


def _terminator_census(root: Path, stunts: dict[str, list[str]]) -> dict[str, int]:
    census: dict[str, int] = {}
    for folder, names in stunts.items():
        for name in names:
            term = terminator(root / folder / f"{name}.sh")
            if term:
                census[term] = census.get(term, 0) + 1
    return census


def describe(root: str | Path = "D:/") -> str:
    """inventory() as something worth reading."""
    info = inventory(root)
    lines = [
        f"card at {info['root']}",
        f"  stunt scripts : {info['stunt_files']} "
        f"({', '.join(f'{k} {v}' for k, v in info['stunts_by_folder'].items())})",
        f"  robot.json    : {info['spec_actions']} actions",
        f"  unknown to spec: {info['unknown_count']}",
        f"  two-folder dupes: {info['two_folder_variants']}",
        f"  trailers      : {info['terminators']}",
        f"  user audio    : {info['user_audio_folder']}/",
    ]
    if info["unknown_to_spec"]:
        lines.append("\n  playable, but robot.json has never heard of them:")
        for row in info["unknown_to_spec"]:
            lines.append(f"    {row['path']}")
    return "\n".join(lines)
