"""Daemon test: run the core over a real JSON-RPC stream pair.

Uses actual sockets over loopback, so this exercises framing, id matching,
notifications and error propagation end to end.
"""
import asyncio
import sys

from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import Report  # noqa: E402

from k1lib.bus import SimBus  # noqa: E402
from k1lib.core import RobotCore  # noqa: E402
from k1lib.wire import StreamClient, StreamServer  # noqa: E402



async def start_server(core):
    """Listen on an ephemeral loopback port and serve the core on it.

    Connections are wrapped so their handler tasks can be cancelled at teardown;
    otherwise the client read loops keep the loop alive and the process hangs.
    """
    tasks: set[asyncio.Task] = set()

    async def handle(reader, writer):
        task = asyncio.create_task(StreamServer(core, reader, writer).run())
        tasks.add(task)
        task.add_done_callback(tasks.discard)

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    return server, port, tasks


report = Report("daemon over a socket pair")
check = report.check


async def main():
    core = RobotCore(SimBus(speed=40.0))
    await core.start()
    server, port, conn_tasks = await start_server(core)

    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    events = []
    client = StreamClient(reader, writer, lambda m, p: events.append((m, p))).start()

    print("-- introspection --")
    methods = await client.call("methods")
    check("methods listed", "action" in methods["methods"], str(len(methods["methods"])))

    print("\n-- state --")
    state = await client.call("state")
    check("battery via rpc", state.get("battery") == 100, str(state.get("battery")))

    print("\n-- action, with notifications --")
    # the core was started before this client connected, so `hello` predates it -
    # what matters is that live notifications arrive
    result = await client.call("action", {"name": "Left Punch"})
    check("action result", result.get("action") == "Left Punch", str(result))
    check("progress notifications arrived", any(m == "progress" for m, _ in events),
          f"{len(events)} events")
    check("notifications carry the action name",
          any(m == "progress" and p.get("action") == "Left Punch" for m, p in events))

    print("\n-- concurrent calls keep ids straight --")
    results = await asyncio.gather(*[
        client.call("volume", {"level": level}) for level in (30, 60, 90)
    ])
    check("3 concurrent volumes answered in order",
          [r["volume"] for r in results] == [30, 60, 90],
          str([r["volume"] for r in results]))

    print("\n-- errors surface as exceptions --")
    for method, params, expect in [
        ("action", {"name": "Nope"}, "unknown action"),
        ("teleport", {}, "unknown intent"),
    ]:
        try:
            await client.call(method, params)
            check(f"{method} raised", False, "no exception")
        except RuntimeError as exc:
            check(f"{method} raised", expect in str(exc), str(exc)[:50])

    print("\n-- daemon survives a client error --")
    state = await client.call("state")
    check("still alive", state.get("battery") == 100)

    print("\n-- a second client shares the same robot --")
    r2, w2 = await asyncio.open_connection("127.0.0.1", port)
    client2 = StreamClient(r2, w2).start()
    check("second client works", (await client2.call("state")).get("battery") == 100)

    await client.close()
    await client2.close()
    # Cancel handlers *before* wait_closed(): in 3.12 it blocks until every
    # connection's handler has finished, and ours are parked on readline().
    for task in conn_tasks:
        task.cancel()
    server.close()
    await asyncio.wait_for(server.wait_closed(), 10)
    check("clean shutdown", True)
    return report.finish()


def run():
    try:
        return asyncio.run(main())
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(run())