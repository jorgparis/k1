"""First-contact probe: find out what the robot actually exposes.

Deliberately passive - connect, enumerate services and characteristics,
subscribe to notifications, ask for state, disconnect. It never sends a joint
move, never enables autoStand, and never triggers an action.

    python tools/probe.py
    python tools/probe.py --name K1-XXXX
    python tools/probe.py --raw        # dump every notification frame verbatim

Run this before anything that could make the robot move.
"""
import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from k1lib import protocol, spec  # noqa: E402


def short_uuid(value) -> str:
    """16-bit shorthand of a 128-bit UUID ('0000ffe0-...' -> 'ffe0').

    The first group is 8 hex digits ('0000ffe0', '00001800'), so the shorthand
    is its last 4. Trailing characters are useless for comparison: every
    Bluetooth-base UUID ends in the same `00001000800000805f9b34fb` tail.
    """
    first_group = str(value).split("-", 1)[0]
    return first_group[-4:] if len(first_group) >= 4 else str(value)


async def main(name_contains: str, seconds: float, raw: bool) -> int:
    from bleak import BleakClient, BleakScanner

    print(f"scanning for {name_contains!r} ...")
    devices = await BleakScanner.discover(timeout=10.0, return_adv=True)
    hits = []
    for address, (device, adv) in devices.items():
        label = device.name or adv.local_name or ""
        if name_contains.lower() in label.lower():
            hits.append((-(adv.rssi or -999), address, label))
    if not hits:
        print("nothing found. Is it on? Is the K One app closed?", file=sys.stderr)
        return 1
    hits.sort()
    _, address, label = hits[0]
    print(f"found {label} at {address}\n")

    frames: list[bytes] = []

    def on_notify(sender, data):
        frames.append(bytes(data))
        if raw:
            print(f"  <- {bytes(data).hex()}")

    client = BleakClient(address)
    try:
        await client.connect()
    except Exception as exc:
        print(f"connect failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        print("If the app is open, close it - BLE allows only one central.", file=sys.stderr)
        return 2

    try:
        print(f"connected: {client.is_connected}\n")

        print("-- services --")
        for service in client.services:
            chars = ", ".join(short_uuid(c.uuid) for c in service.characteristics)
            print(f"  {short_uuid(service.uuid)}  chars: {chars or '(none)'}")

        want_svc = spec.service_uuid()
        want_char = spec.characteristic_uuid()
        have_service = any(s.uuid.lower() == want_svc.lower() for s in client.services)
        have_char = any(c.uuid.lower() == want_char.lower()
                        for s in client.services for c in service_characteristics(s))
        print(f"\nspec service {short_uuid(want_svc)} present: {have_service}")
        print(f"spec char    {short_uuid(want_char)} present: {have_char}")

        print("\n-- subscribing to notifications --")
        try:
            await client.start_notify(want_char, on_notify)
            print(f"  subscribed to {want_char}")
        except Exception as exc:
            print(f"  could not subscribe ({type(exc).__name__}: {exc})")

        print("\n-- asking for state --")
        await client.write_gatt_char(want_char, protocol.packet(spec.code("state")), response=False)
        print(f"  sent state request {protocol.packet(spec.code('state')).hex()}, listening {seconds}s")

        deadline = asyncio.get_running_loop().time() + seconds
        while asyncio.get_running_loop().time() < deadline and not frames:
            await asyncio.sleep(0.1)

        if not frames:
            print("  no notification arrived within the window")
        else:
            print(f"\n-- {len(frames)} notification(s) --")
            for blob in frames[:20]:
                pkt = protocol.parse(blob)
                if pkt is None:
                    print(f"  {blob.hex()}  (not a K1 packet)")
                    continue
                flag = "ok" if pkt.valid else "BAD CHECKSUM"
                detail = json.dumps(pkt.struct) if pkt.struct else (
                    f"text={pkt.text()!r}" if pkt.text() else f"data={blob[4:-1].hex()}")
                print(f"  type={pkt.type_name:<12} [{flag}] {detail}")
            if raw:
                print("\nverbatim:")
                for blob in frames[:20]:
                    print(f"  {blob.hex()}")
    finally:
        print("\ndisconnecting ...")
        try:
            await client.disconnect()
        except Exception:
            pass
    print("done - the robot was not asked to move")
    return 0


def service_characteristics(service):
    return getattr(service, "characteristics", [])


def cli() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--name", default="K1", help="BLE name fragment to look for")
    ap.add_argument("--seconds", type=float, default=6.0, help="how long to listen for replies")
    ap.add_argument("--raw", action="store_true", help="dump every notification frame verbatim")
    args = ap.parse_args()
    try:
        return asyncio.run(main(args.name, args.seconds, args.raw))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(cli())