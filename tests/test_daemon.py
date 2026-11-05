"""The daemon as users actually run it: `python -m k1lib.wire --bus sim`.

Runs the core in a real subprocess and talks to it over stdin/stdout, so this
covers the stdio wiring, EOF handling and the drain-on-shutdown behaviour that
an in-process socket test cannot see.
"""
import json
import subprocess
import sys

from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import ROOT, Report  # noqa: E402



def talk(requests, timeout=60, fast=True):
    """Pipe requests into the daemon, collect every JSON line it writes.

    `fast` compresses the simulated durations. The frames and replies are
    identical; only the wall clock differs, so the suite does not spend seconds
    waiting out real 800ms stunts.
    """
    payload = "".join(json.dumps(r) + "\n" for r in requests)
    cmd = [sys.executable, "-u", "-m", "k1lib.wire", "--bus", "sim"]
    if fast:
        cmd += ["--speed", "60", "--time-scale", "0.002"]
    proc = subprocess.run(
        cmd,
        input=payload, cwd=ROOT, capture_output=True, text=True, timeout=timeout,
    )
    replies, events = {}, []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        message = json.loads(line)
        if "id" in message:
            replies[message["id"]] = message
        else:
            events.append(message)
    return replies, events, proc


report = Report("daemon over stdio")
check = report.check


def main():
    print("-- a normal session over stdio --")
    replies, events, proc = talk([
        {"jsonrpc": "2.0", "id": 1, "method": "methods"},
        {"jsonrpc": "2.0", "id": 2, "method": "volume", "params": {"level": 55}},
        {"jsonrpc": "2.0", "id": 3, "method": "pose", "params": {"pose": {"head": 150}, "speed": 35}},
        {"jsonrpc": "2.0", "id": 4, "method": "say", "params": {"clip": 103}},
    ])
    check("all four requests answered", sorted(replies) == [1, 2, 3, 4], f"got {sorted(replies)}")
    check("methods listed", "action" in replies[1]["result"]["methods"])
    check("volume applied", replies[2]["result"]["volume"] == 55)
    check("pose echoed", replies[3]["result"]["pose"]["head"] == 150)
    check("say echoed", replies[4]["result"]["clip"] == 103)
    check("exit clean", proc.returncode == 0, f"rc={proc.returncode}")

    print("\n-- errors come back as JSON-RPC errors --")
    replies, _, proc = talk([
        {"jsonrpc": "2.0", "id": 1, "method": "action", "params": {"name": "No Such Action"}},
        {"jsonrpc": "2.0", "id": 2, "method": "teleport", "params": {}},
    ])
    check("both answered", sorted(replies) == [1, 2])
    check("unknown action is an error", "error" in replies[1], str(replies[1].get("error")))
    check("unknown intent is an error", "unknown intent" in replies[2]["error"]["message"])
    check("daemon survived the errors", proc.returncode == 0)

    print("\n-- drain: a request in flight when stdin closes still gets a reply --")
    # `say` completes almost instantly; the point is that EOF does not cancel it.
    replies, _, proc = talk([
        {"jsonrpc": "2.0", "id": 1, "method": "say", "params": {"clip": 101}},
        {"jsonrpc": "2.0", "id": 2, "method": "volume", "params": {"level": 20}},
    ])
    check("no reply lost to EOF", sorted(replies) == [1, 2], f"got {sorted(replies)}")

    print("\n-- malformed input does not kill the daemon --")
    payload = "not json\n\n{}\n" + json.dumps(
        {"jsonrpc": "2.0", "id": 9, "method": "volume", "params": {"level": 10}}) + "\n"
    proc = subprocess.run(
        [sys.executable, "-u", "-m", "k1lib.wire", "--bus", "sim"],
        input=payload, cwd=ROOT, capture_output=True, text=True, timeout=60,
    )
    check("survived junk lines", '"id":9' in proc.stdout.replace(" ", ""), proc.stdout.strip()[:80])
    check("exit clean after junk", proc.returncode == 0)

    print("\n-- pipelined actions all survive (the lost-punch regression) --")
    # This is the exact shape that lost a punch on real hardware: a client that
    # writes several commands back to back without waiting for replies. The
    # daemon used to pass them straight through, so the second action landed
    # while the first was still animating and was dropped with no error. The
    # command queue must serialise them instead.
    replies, events, proc = talk([
        {"jsonrpc": "2.0", "id": 1, "method": "action", "params": {"name": "Left Punch"}},
        {"jsonrpc": "2.0", "id": 2, "method": "action", "params": {"name": "Right Punch"}},
        {"jsonrpc": "2.0", "id": 3, "method": "action", "params": {"name": "Left Punch"}},
    ], timeout=120)
    check("every pipelined action got a reply", sorted(replies) == [1, 2, 3],
          f"got {sorted(replies)}")
    completions = [replies[i]["result"].get("completion") for i in (1, 2, 3)]
    check("none reported a timeout", "timeout" not in completions, str(completions))
    check("all three report real completion",
          all(c == "progress-100" for c in completions), str(completions))
    done = [e["params"]["action"] for e in events
            if e.get("method") == "progress" and e["params"].get("percent") == 100]
    check("all three actually finished, not just all three replied",
          sorted(done) == ["Left Punch", "Left Punch", "Right Punch"], str(done))

    print("\n-- a poll mixed into a pipelined batch does not break it --")
    replies, _, proc = talk([
        {"jsonrpc": "2.0", "id": 1, "method": "action", "params": {"name": "Left Punch"}},
        {"jsonrpc": "2.0", "id": 2, "method": "state"},
        {"jsonrpc": "2.0", "id": 3, "method": "action", "params": {"name": "Right Punch"}},
    ], timeout=120)
    check("action still completed while a poll was queued",
          replies[1]["result"].get("completion") == "progress-100",
          str(replies[1]["result"])[:80])
    check("the poll still answered with real state",
          isinstance(replies[2]["result"], dict) and "battery" in replies[2]["result"],
          str(replies[2]["result"])[:80])
    check("the second action was not dropped",
          replies[3]["result"].get("completion") == "progress-100",
          str(replies[3]["result"])[:80])
    return report.finish()


if __name__ == "__main__":
    sys.exit(main())