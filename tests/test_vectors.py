"""Cross-validate the Python codec against golden vectors produced by the
reference JS implementation (robosen-js). Run: python tests/test_vectors.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import harness  # noqa: F401,E402  (UTF-8 stdout + quiet k1 loggers)

from k1lib import protocol as p
from k1lib import spec

# Captured from robosen-js on node v24 (see tools/dump_vectors.js)
VECTORS = [
    ("state request", p.packet("0f"), "ffff020f11"),
    ("volume 100", p.packet("0d", 100), "ffff030d6474"),
    ("move north", p.packet("01"), "ffff020103"),
    ("action ProAction", p.packet("17", "ProAction/Say Hello"),
     "ffff151750726f416374696f6e2f5361792048656c6c6f2b"),
    ("action group", p.packet("17", "Action/Cyberpunk"),
     "ffff1217416374696f6e2f437962657270756e6b69"),
    ("recording", p.packet("17", "recordings/foo"), "ffff10177265636f7264696e67732f666f6fca"),
    ("audio", p.packet("19", "AppSysMS/101"), "ffff0e194170705379734d532f313031e8"),
    ("shutdown", p.packet("fa"), "ffff02fafc"),
    ("autoStand on", p.packet("11", True), "ffff03110115"),
    ("joint move", p.packet("e8", {"leftThigh": 129, "head": 122, "speed": 40}),
     "ffff1be8810000000000000000000000000000007a000000000000002826"),
    ("lock struct", p.packet("ed", {"leftThigh": 1}),
     "ffff13ed010000000000000000000000000000000001"),
    ("program", p.packet("e6", {}),
     "ffff1be60000000000000000000000000000000000000000000000000001"),
]

failures = 0
for name, got, want in VECTORS:
    got_hex = got.hex()
    ok = got_hex == want
    failures += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {name:18} {got_hex}" + ("" if ok else f"\n      want {want}"))

# round-trip the parser
print("\n-- parser round-trip --")
for name, got, _ in VECTORS:
    parsed = p.parse(got)
    ok = parsed is not None and parsed.valid
    failures += not ok
    extra = f" type={parsed.type_name} text={parsed.text()!r}" if parsed and len(parsed.data) > 1 else ""
    print(f"{'PASS' if ok else 'FAIL'}  {name:18}{extra}")

print("\n-- corrupt packet is rejected --")
bad = bytearray(p.packet("0f"))
bad[-1] ^= 0xFF
parsed = p.parse(bytes(bad))
ok = parsed is not None and not parsed.valid
failures += not ok
print(f"{'PASS' if ok else 'FAIL'}  bad checksum detected")

print("\n-- spec sanity --")
print(f"  joints        : {len(spec.joints())}")
print(f"  joint struct  : {spec.joint_size()} bytes")
print(f"  actions       : {len(spec.actions())}")
print(f"  service uuid  : {spec.service_uuid()}")
print(f"  char uuid     : {spec.characteristic_uuid()}")
print(f"  neutral head  : {spec.joints()['head']['neutral']}")
fails = [n for n, _, _ in VECTORS]
if len(spec.joints()) != 17 or spec.joint_size() != 25:
    print("FAIL  unexpected joint table shape")
    failures += 1

print(f"\n{'ALL PASS' if not failures else f'{failures} FAILURE(S)'}")
sys.exit(1 if failures else 0)