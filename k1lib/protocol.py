"""Robosen K1 packet codec. Pure stdlib - no bleak needed to build/parse packets.

Wire format (verified against the reference JS implementation):

    +--------+---------+--------+---------+----------+
    | header | numByte | type   | data... | checksum |
    | ff ff  | 1 byte  | 1 byte | 0..n    | 1 byte   |
    +--------+---------+--------+---------+----------+

numBytes = 1 (type) + len(data) + 1 (checksum)
checksum = sum(numBytes, type, *data) % 256
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from . import spec

log = logging.getLogger("k1.protocol")


def checksum(body: bytes) -> int:
    return sum(body) % 256


def encode_value(type_cfg: dict, data) -> bytes:
    """Encode the data segment for a given type config (mirrors Robot.js #encode)."""
    if isinstance(data, bool):
        return bytes([1 if data else 0])
    if isinstance(data, int):
        return bytes([data & 0xFF])
    if isinstance(data, (bytes, bytearray)):
        return bytes(data)
    if isinstance(data, dict) and type_cfg.get("struct"):
        return encode_struct(spec.load()[type_cfg["value"]], data, type_cfg.get("length"))
    return str(data).encode("utf-8")


def _entries(struct_cfg: dict) -> list[tuple[str, dict]]:
    """(name, entry) pairs. robot.json carries the name as the dict key, not a field."""
    return [
        (key, entry)
        for key, entry in struct_cfg.items()
        if isinstance(entry, dict) and entry.get("index") is not None
    ]


def encode_struct(struct_cfg: dict, values: dict, length: int | None = None) -> bytes:
    """Build a fixed-width byte struct from a {name: value} mapping."""
    entries = _entries(struct_cfg)
    size = length or (max(entry["index"] for _, entry in entries) + 1)
    buf = bytearray(size)
    for name, entry in entries:
        idx = entry["index"]
        if idx < size:
            buf[idx] = int(values.get(name, 0)) & 0xFF
    return bytes(buf)


def packet(type_code: str, data=None) -> bytes:
    """Build a complete frame.

    `data=None` means "no payload", which is correct for request packets like
    `state` (whose struct arrives in the reply, not the request). But a *joint*
    struct is always sent populated - building one empty would put an all-zero
    pose on the wire, so that is refused instead.
    """
    type_code = type_code.lower()
    found = spec.type_by_code(type_code)
    type_cfg = found[1] if found else {}
    if data is None and type_cfg.get("value") == "joint":
        raise ValueError(
            f"{type_code} ({found[0] if found else '?'}) carries a joint struct; "
            f"pass protocol.joint_struct() explicitly"
        )
    payload = encode_value(type_cfg, data if data is not None else "")
    num_bytes = bytes([1 + len(payload) + 1])
    body = num_bytes + bytes.fromhex(type_code) + payload
    return spec.header() + body + bytes([checksum(body)])


def joint_struct(poses: dict | None = None, speed: int | None = None) -> dict:
    """Build a full joint mapping: caller overrides on top of neutral + fixed.

    Names that are not in the spec are ignored rather than carried along, so a
    typo can never end up in the caller's long-lived joint state.
    """
    out: dict[str, int] = {name: e["neutral"] for name, e in spec.joints().items()}
    out.update(spec.fixed_joints())
    for name, value in (poses or {}).items():
        if name in out:
            out[name] = int(value)
    if speed is not None:
        out["speed"] = int(speed)
    return out


def clamp_pose(poses: dict) -> dict:
    """Clamp every named joint to its documented min/max.

    Names that are not in the spec are dropped (they cannot corrupt the
    fixed-width struct) but are logged, so a typo like 'headl' surfaces instead
    of doing nothing. Fixed filler slots and `speed` pass through unchanged.
    """
    limits = spec.joints()
    out = {}
    for key, value in poses.items():
        if key in limits:
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                log.warning("joint %r: %r is not a number, ignored", key, value)
                continue
            lo, hi = limits[key]["min"], limits[key]["max"]
            clamped = max(lo, min(hi, int(round(value))))
            if clamped != value:
                log.warning("%s=%s out of range [%d,%d] -> %d", key, value, lo, hi, clamped)
            out[key] = clamped
        elif key in spec.fixed_joints() or key == "speed":
            out[key] = value
        else:
            log.warning("unknown joint %r ignored (see: python k1ctl.py joints)", key)
    return out


# --- parsing ----------------------------------------------------------------
@dataclass
class Packet:
    valid: bool
    type: str
    data: bytes
    num_bytes: int
    checksum: int
    struct: dict | None = None
    raw: bytes = field(default=b"", repr=False)

    @property
    def type_name(self) -> str:
        found = spec.type_by_code(self.type)
        return found[0] if found else "?"

    def text(self) -> str:
        return self.data.decode("utf-8", errors="replace")

    def value(self):
        """Single-byte payloads come back as ints, longer ones as text."""
        if len(self.data) == 1:
            return self.data[0]
        return self.text()


def parse(frame: bytes) -> Packet | None:
    """Parse a notification frame. Returns None if it is not a K1 packet."""
    if len(frame) < 5 or frame[:2] != spec.header():
        return None
    num_bytes = frame[2]
    body = frame[2 : 2 + num_bytes]
    if len(body) != num_bytes:
        return None
    type_code = body[1:2].hex()
    data = body[2:]
    got = frame[-1]
    struct = None
    found = spec.type_by_code(type_code)
    type_cfg = found[1] if found else None
    if type_cfg and type_cfg.get("struct") and len(data) > 1:
        struct = decode_struct(spec.load()[type_cfg["value"]], data, type_cfg.get("length"))
    return Packet(
        valid=checksum(body) == got,
        type=type_code,
        data=data,
        num_bytes=num_bytes,
        checksum=got,
        struct=struct,
        raw=frame,
    )


def decode_struct(struct_cfg: dict, data: bytes, length: int | None = None) -> dict:
    out = {}
    for name, entry in _entries(struct_cfg):
        idx = entry["index"]
        if length is not None and idx >= length:
            continue
        out[name] = data[idx] if idx < len(data) else 0
    return out