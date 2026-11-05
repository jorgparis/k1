"""JSON-RPC 2.0 over a byte stream, one object per line.

This is the seam that lets the core be a daemon. `serve_stdio()` runs a
RobotCore on stdin/stdout; `StreamClient` talks to it. Swap the streams for
unix sockets or TCP and the same code drives the robot from another process or
another machine.

    {"jsonrpc":"2.0","id":1,"method":"action","params":{"name":"Left Punch"}}
    {"jsonrpc":"2.0","id":1,"result":{"action":"Left Punch","duration_ms":800}}
    {"jsonrpc":"2.0","method":"progress","params":{"action":"Left Punch","percent":50}}

Note that RPC `params` use intent *parameter* names (`name`, `direction`,
`clip`, ...), which differ from the friendly keys used in routine files
(`action`, `move`, `say`). See core.STEP_INTENTS for that mapping.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import threading
from typing import Any, Callable

from .core import RobotCore

log = logging.getLogger("k1.wire")

MAX_LINE = 1 << 20  # 1 MiB - a routine is small, a runaway client is not
DRAIN_TIMEOUT = 120.0  # longest single action is ~58s; allow a bit of headroom


def encode(message: dict) -> bytes:
    return (json.dumps(message, separators=(",", ":")) + "\n").encode("utf-8")


def decode(line: bytes) -> dict | None:
    line = line.strip()
    if not line:
        return None
    try:
        return json.loads(line)
    except json.JSONDecodeError:
        log.warning("bad JSON line: %r", line[:120])
        return None


def make_response(req_id: Any, result: Any = None, error: str | None = None) -> dict:
    if error is not None:
        return {"jsonrpc": "2.0", "id": req_id,
                "error": {"code": -32000, "message": error}}
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


class StreamClient:
    """Sends intents over a stream and matches replies by id.

    Server-initiated notifications (messages with no `id`) go to `on_event`.
    """

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
                 on_event: Callable[[str, dict], None] | None = None):
        self.reader = reader
        self.writer = writer
        self.on_event = on_event
        self._pending: dict[int, asyncio.Future] = {}
        self._next_id = 1
        self._reader_task: asyncio.Task | None = None

    def start(self) -> "StreamClient":
        if self._reader_task is None:
            self._reader_task = asyncio.create_task(self._read_loop())
        return self

    async def _read_loop(self):
        try:
            while line := await self.reader.readline():
                message = decode(line)
                if not message:
                    continue
                if "id" in message and "method" not in message:
                    future = self._pending.pop(message["id"], None)
                    if future and not future.done():
                        future.set_result(message)
                elif message.get("method") and self.on_event:
                    self.on_event(message["method"], message.get("params") or {})
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("client read loop died")
        finally:
            # Nothing is going to answer these now - fail loudly instead of hanging.
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(ConnectionError("stream closed"))
            self._pending.clear()

    async def call(self, method: str, params: dict | None = None, timeout: float = 30.0):
        req_id = self._next_id
        self._next_id += 1
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[req_id] = future
        self.writer.write(encode({"jsonrpc": "2.0", "id": req_id,
                                  "method": method, "params": params or {}}))
        await self.writer.drain()
        try:
            message = await asyncio.wait_for(future, timeout=timeout)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            self._pending.pop(req_id, None)
            raise
        if "error" in message:
            raise RuntimeError(message["error"].get("message", "unknown error"))
        return message.get("result")

    async def close(self):
        if self._reader_task:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except (asyncio.CancelledError, Exception):
                pass
            self._reader_task = None
        self.writer.close()
        try:
            await self.writer.wait_closed()
        except Exception:
            pass


class StreamServer:
    """Serves a RobotCore over a stream pair."""

    def __init__(self, core: RobotCore, reader: asyncio.StreamReader,
                 writer: asyncio.StreamWriter):
        self.core = core
        self.reader = reader
        self.writer = writer
        self._seq = 0
        self._tasks: set[asyncio.Task] = set()

    def _notify(self, method: str, params: dict):
        self._seq += 1
        self.writer.write(encode({"jsonrpc": "2.0", "method": method, "params": params}))

    async def handle(self, message: dict):
        req_id = message.get("id")
        method = message.get("method")
        params = message.get("params") or {}
        if method == "methods":
            reply = make_response(req_id, {"methods": sorted(self.core.HANDLERS)})
        else:
            outcome = await self.core.run(method, params)
            reply = (make_response(req_id, outcome["result"]) if outcome.get("ok")
                     else make_response(req_id, error=outcome["error"]))
        self.writer.write(encode(reply))

    async def run(self):
        self.core.on(self._notify)
        try:
            while line := await self.reader.readline():
                if len(line) > MAX_LINE:
                    self.writer.write(encode(make_response(None, error="line too long")))
                    await self.writer.drain()
                    continue
                message = decode(line)
                if not message:
                    continue
                # handle each request in its own task so a slow action (which
                # waits for completion) cannot block unrelated calls
                task = asyncio.create_task(self.handle(message))
                self._tasks.add(task)
                task.add_done_callback(self._tasks.discard)
        finally:
            # Input closed. Drain in-flight requests rather than cancelling
            # them: a client that writes everything then waits for replies must
            # still get answers to the slow ones (e.g. a 58s action).
            if self._tasks:
                log.info("draining %d in-flight request(s)", len(self._tasks))
                await asyncio.wait(set(self._tasks), timeout=DRAIN_TIMEOUT)
            for task in list(self._tasks):
                task.cancel()
            # Always close the socket. asyncio's Server.wait_closed() blocks
            # until every connection's transport is gone, so leaving the writer
            # open here wedges anything waiting on shutdown.
            self.writer.close()


class _BinaryWriter:
    """StreamWriter-shaped writer over a real binary file object.

    asyncio cannot give us a StreamWriter for stdout on Windows (the Proactor
    loop has no `connect_write_pipe` for pipes), but StreamServer only needs
    `write` and `drain`, so a thin adapter over the raw buffer is enough. Going
    through `.buffer` also sidesteps the console code page: on a cp1252 console
    a robot name or joint label outside that charset would otherwise raise
    UnicodeEncodeError.
    """

    def __init__(self, stream):
        self._stream = stream

    def write(self, data: bytes):
        self._stream.write(data)

    async def drain(self):
        self._stream.flush()

    def close(self):
        try:
            self._stream.flush()
        except Exception:
            pass


async def serve_stdio(core: RobotCore):
    """Run the core on stdin/stdout - `python -m k1lib.wire --bus sim`.

    stdin is pumped by a thread rather than `loop.connect_read_pipe`, because
    that API is unavailable for pipes on Windows' default Proactor event loop.
    Feeding a StreamReader from the thread keeps StreamServer unchanged and
    behaves the same on both platforms.
    """
    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader()

    def pump():
        try:
            for line in sys.stdin.buffer:
                loop.call_soon_threadsafe(reader.feed_data, line)
        except Exception:
            log.exception("stdin reader died")
        finally:
            loop.call_soon_threadsafe(reader.feed_eof)

    threading.Thread(target=pump, daemon=True, name="k1-stdin").start()
    await core.start()
    await StreamServer(core, reader, _BinaryWriter(sys.stdout.buffer)).run()


def main():  # pragma: no cover
    ap = argparse.ArgumentParser(description="Run the K1 core as a JSON-RPC daemon over stdio")
    ap.add_argument("--bus", default="sim", choices=["sim", "ble"])
    ap.add_argument("--name", default="K1", help="BLE name fragment (ble bus only)")
    ap.add_argument("--speed", type=float, default=1.0,
                    help="sim only: run animations this many times faster, so "
                         "tests need not wait out real stunt durations")
    ap.add_argument("--time-scale", type=float, default=1.0,
                    help="sim only: compress pacing sleeps (moves, routine gaps)")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(name)s %(message)s")
    from .core import make_bus

    bus = make_bus(args.bus, args.name)
    if args.bus == "sim":
        bus.speed_factor = args.speed
    core = RobotCore(bus)
    core.time_scale = args.time_scale

    try:
        asyncio.run(serve_stdio(core))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":  # pragma: no cover
    main()