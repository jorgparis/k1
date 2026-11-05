"""Play every action and measure how long the robot actually takes.

`robot.json` gives every stunt a `duration_ms`, but that is a declaration, not a
measurement: the firmware may finish early, run long, or never report at all.
This module sends the actions one at a time - exactly as the command queue
already does - and records what came back.

Two clocks are kept per action, because they answer different questions:

* `firmware_ms` - from the action frame being written to the robot reporting
  `progress-100`. This is the closest thing to "how long the stunt ran" that
  BLE can tell us, and it is the number to compare with `robot.json`.
* `measured_ms` - from the write to the wait returning. It includes the core's
  50 ms polling granularity and any time the queue took to drain, so it is an
  upper bound on the same interval.

Both are measured from the `action` event, which the core emits immediately
after the write, so a slow `progress` notification is attributable to the
robot rather than to this module.

The robot runs one action at a time and a second frame sent mid-stunt is dropped
without complaint, so the benchmark **never writes a joint frame between
actions** (see `run()` for why the vendor neutral is unsafe here). `run()` stops
at the first `timeout`: the queue has been released while the robot may still be
moving, and sending the next action would measure a dropped frame rather than a
stunt.

Nothing here writes to the SD card. `--card-root` only reads the file listing so
the card-only `SpeAction/` scripts can be benchmarked too.
"""
from __future__ import annotations

import asyncio
import csv
import dataclasses
import json
import statistics
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable

from . import sdcard, spec

# Pause after an action reports `progress-100`, before the next frame is sent.
# Hardware finding (K1-XXXX, 2026-10-30): `progress-100` is the end of the
# progress *stream*, not proof that the firmware will accept another action. In
# a 5-action batch with no pause, only the first one was performed; with 3s the
# second was ambiguous; with 6s every action performed and the owner could see
# the gap. 6 is therefore the default, not a tuning knob to lower casually.
SETTLE_S = 6.0


def norm(name: str) -> str:
    """Card filenames differ from robot.json in case and punctuation."""
    return "".join(ch for ch in name.lower() if ch.isalnum())


def parse_verdicts(raw: str) -> dict[str, str]:
    """Split ``--set`` text into ``{name: yes|no|unsure}``.

    Comma is the separator, but action names contain commas too (`Bow, Please`,
    `Kneel,Please`). A comma only ends a verdict when the fragment before it
    carries an ``=``; fragments without one are glued back onto the next, so
    ``"Bow, Please=yes,Left Punch=no"`` is two verdicts, not three errors. A
    semicolon is always a hard separator.

    Raises ``ValueError`` naming the fragment that could not be read.
    """
    verdicts: dict[str, str] = {}
    for group in raw.split(";"):
        pending: list[str] = []
        for part in group.split(","):
            pending.append(part)
            if "=" not in part:
                continue
            item = ",".join(pending)
            pending = []
            name, _, value = item.rpartition("=")
            value = value.strip().lower()
            if not name.strip() or value not in ("yes", "no", "unsure"):
                raise ValueError(item.strip())
            verdicts[name.strip()] = value
        if any(part.strip() for part in pending):
            raise ValueError(",".join(pending).strip())
    return verdicts


@dataclass(frozen=True)
class Entry:
    """One thing worth playing, and the clock robot.json declares for it."""

    name: str
    path: str
    folder: str
    declared_ms: int | None
    source: str = "spec"          # "spec" or "card"
    description: str = ""

    @property
    def declared_known(self) -> bool:
        return self.declared_ms is not None


@dataclass
class Row:
    """What actually happened when one entry was played."""

    name: str
    path: str
    folder: str
    source: str
    declared_ms: int | None
    measured_ms: float | None
    firmware_ms: float | None
    completion: str
    progress: int | None
    ok: bool
    error: str | None = None
    started: bool | None = None      # did the robot report it running?
    trace: list | None = None        # (ms after write, percent) packets
    confirmed: str | None = None     # a human's verdict, when one watched

    @property
    def ratio(self) -> float | None:
        """Measured firmware time divided by the declared clock."""
        if not self.declared_ms or self.firmware_ms is None:
            return None
        return self.firmware_ms / self.declared_ms

    @property
    def completed(self) -> bool:
        return self.completion == "progress-100"

    def to_dict(self) -> dict:
        out = asdict(self)
        out["ratio"] = round(self.ratio, 4) if self.ratio is not None else None
        out["completed"] = self.completed
        return out


def _terms(value: str | Iterable[str] | None) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        parts = value.replace(";", ",").split(",")
    else:
        parts = list(value)
    return [str(p).strip().lower() for p in parts if str(p).strip()]


def _matches(entry: Entry, terms: list[str]) -> bool:
    if not terms:
        return True
    haystack = f"{entry.name} {entry.path}".lower()
    return any(term in haystack for term in terms)


def entries(card_root: str | Path | None = None,
            only: str | Iterable[str] | None = None,
            skip: str | Iterable[str] | None = None,
            order: str = "name",
            limit: int | None = None) -> list[Entry]:
    """The spec's actions, plus the card's scripts when `card_root` is given.

    The card adds `SpeAction/`, which `robot.json` never mentions. Names are
    matched on letters and digits only, so `Bow, please` and `Bow, Please` are
    one action rather than two. Card scripts the spec does know inherit the
    declared duration; the rest carry `declared_ms=None` and are still playable
    by path, with the core's 2s placeholder as their completion deadline.
    """
    found: dict[str, Entry] = {}
    for name, e in spec.actions().items():
        found[e["path"].lower()] = Entry(
            name=name, path=e["path"], folder=e["folder"],
            declared_ms=e["duration_ms"], source="spec",
            description=e.get("description", ""),
        )

    if card_root:
        by_norm = {norm(n): (n, e) for n, e in spec.actions().items()}
        for item in sdcard.stunt_paths(card_root):
            key = item["path"].lower()
            if key in found:
                continue
            name, e = by_norm.get(norm(item["name"]), (item["name"], None))
            found[key] = Entry(
                name=name if e else item["name"],
                path=item["path"],
                folder=item["folder"],
                declared_ms=e["duration_ms"] if e else None,
                source="spec" if e else "card",
                description=e.get("description", "") if e else "",
            )

    rows = list(found.values())
    only_terms, skip_terms = _terms(only), _terms(skip)
    rows = [e for e in rows
            if _matches(e, only_terms)
            and not (skip_terms and _matches(e, skip_terms))]

    if order == "duration":
        rows.sort(key=lambda e: (e.declared_ms is None, e.declared_ms or 0, e.name.lower()))
    elif order == "folder":
        rows.sort(key=lambda e: (e.folder.lower(), e.name.lower()))
    elif order == "name":
        rows.sort(key=lambda e: e.name.lower())
    else:
        raise ValueError(f"unknown order {order!r}; try name, duration or folder")

    if limit is not None and limit >= 0:
        rows = rows[:limit]
    return rows


def describe_plan(rows: list[Entry]) -> str:
    known = sum(1 for e in rows if e.declared_ms is not None)
    total = sum(e.declared_ms for e in rows if e.declared_ms is not None) / 1000
    head = f"{len(rows)} actions to play"
    if known:
        head += f", {total:.1f}s of declared animation"
    if known != len(rows):
        head += f" ({known}/{len(rows)} with a robot.json clock)"
    lines = [
        head,
        f"each measured from the write to progress-100; no neutral stand is "
        f"inserted between actions, and each is followed by a "
        f"{SETTLE_S:.0f}s settle pause before the next frame",
        "",
    ]
    width = max((len(e.name) for e in rows), default=5)
    width = min(max(width, 5), 34)
    for i, e in enumerate(rows, 1):
        clock = f"{e.declared_ms / 1000:5.1f}s" if e.declared_ms is not None else "    ?"
        lines.append(f"{i:4}  {e.name[:width]:<{width}}  {clock}  {e.path}")
    return "\n".join(lines)


def _row_line(index: int, total: int, row: Row) -> str:
    fw = f"{row.firmware_ms:7.0f}ms" if row.firmware_ms is not None else "      -  "
    meas = f"{row.measured_ms:7.0f}ms" if row.measured_ms is not None else "      -  "
    ratio = f"x{row.ratio:.2f}" if row.ratio is not None else "  -  "
    note = row.completion
    if row.started is False:
        note += "  (never reported running)"
    if row.confirmed:
        note += f"  seen={row.confirmed}"
    return (f"[{index:3}/{total}] {row.name[:34]:<34} "
            f"fw {fw}  wait {meas}  {ratio:>7}  {note}")


async def run(core, rows: list[Entry], *,
              gap: float = SETTLE_S,
              max_wait: float | None = None,
              stop_on_timeout: bool = True,
              on_row: Callable[[Row], None] | None = None,
              log: Callable[[str], None] | None = print) -> list[Row]:
    """Play every entry through `core`, one at a time, and time each one.

    `core` is a `RobotCore`, already connected. `max_wait` is passed as the
    per-call `grace_s`, replacing the duration-aware deadline so a robot that
    has stopped reporting cannot hang the whole run.

    Every number comes from the core's own completion detail, not from a second
    listener here. That matters: the core is the only place that knows whether
    the 100% packet followed a start packet, so it is the only place that can
    tell a finished stunt from a stray packet. A row with `started=False` was
    never seen running, whatever its `completion` says.

    **No `jointMove` is ever sent between actions.** `robot.json`'s neutral is
    not neutral: on hardware it leans right and forward, drives `rightArm` to
    its documented minimum (34), and was followed by the robot reporting a
    stuck right shoulder. An action sent after that frame was also ignored by
    the firmware. So the benchmark leaves each action's end pose alone and lets
    the firmware own the stance. `gap` pauses after an action if you want the
    robot to settle; the progress signal already says when it finished.

    `gap` defaults to `SETTLE_S` (6s) and is **not** cosmetic. `progress-100`
    ends the progress stream, but the firmware still refuses a new action for a
    moment: with no pause, a 5-action batch performed only the first one. Every
    frame after a 6s pause was performed. Lowering this re-creates the drops.

    Stops after the first timeout, because the queue is released while the
    robot may still be animating. Pass `stop_on_timeout=False` only if the
    subsequent numbers do not matter to you.
    """
    results: list[Row] = []
    for index, entry in enumerate(rows, 1):
        call_started = time.monotonic()
        params: dict = {"path": entry.path, "wait": True}
        if max_wait is not None:
            params["grace_s"] = max_wait
        answer = await core.run("actionPath", params)
        returned = time.monotonic()

        result = answer.get("result") or {}
        elapsed = result.get("elapsed_ms")
        if elapsed is None:                       # the call failed early
            elapsed = (returned - call_started) * 1000.0
        completion = (result.get("completion")
                      or ("error" if not answer.get("ok") else "unknown"))
        row = Row(
            name=entry.name,
            path=entry.path,
            folder=entry.folder,
            source=entry.source,
            declared_ms=entry.declared_ms,
            measured_ms=float(elapsed),
            firmware_ms=result.get("firmware_ms"),
            completion=completion,
            progress=result.get("progress"),
            ok=bool(answer.get("ok")),
            error=answer.get("error"),
            started=result.get("saw_start"),
            trace=result.get("trace"),
        )
        results.append(row)
        if on_row:
            on_row(row)
        if log:
            log(_row_line(index, len(rows), row))
            if row.error:
                log(f"        error: {row.error}")

        if row.completion == "timeout":
            if stop_on_timeout:
                if log:
                    log("        stopped: the queue was released while the robot may "
                        "still be animating. Check it before running again.")
                break
            if log:
                log("        continuing after timeout (--keep-going); later results "
                    "may be a dropped frame, not a stunt")
        if gap:
            await asyncio.sleep(gap)
    return results


def summarise(results: list[Row]) -> dict:
    done = [r for r in results if r.completed]
    ratios = [r.ratio for r in done if r.ratio is not None]
    declared = [r.declared_ms for r in results if r.declared_ms]
    firmware = [r.firmware_ms for r in results if r.firmware_ms is not None]
    measured = [r.measured_ms for r in results if r.measured_ms is not None]
    return {
        "actions": len(results),
        "completed": len(done),
        "timeouts": sum(1 for r in results if r.completion == "timeout"),
        "quiet": sum(1 for r in results if r.completion == "quiet"),
        "unstarted": sum(1 for r in results if r.started is False),
        "errors": sum(1 for r in results if not r.ok),
        "confirmed_yes": sum(1 for r in results if r.confirmed == "yes"),
        "confirmed_no": sum(1 for r in results if r.confirmed == "no"),
        "unconfirmed": sum(1 for r in results if not r.confirmed),
        "declared_ms": sum(declared),
        "firmware_ms": round(sum(firmware), 1) if firmware else 0.0,
        "measured_ms": round(sum(measured), 1) if measured else 0.0,
        "ratio_median": round(statistics.median(ratios), 4) if ratios else None,
        "ratio_min": round(min(ratios), 4) if ratios else None,
        "ratio_max": round(max(ratios), 4) if ratios else None,
    }


def table(results: list[Row], summary: dict | None = None) -> str:
    """The results as something worth reading, worst clock first."""
    summary = summary or summarise(results)
    ordered = sorted(results, key=lambda r: (r.ratio is None, -(r.ratio or 0)))
    watched = any(r.confirmed for r in results)
    seen_head = f" {'seen':<6}" if watched else ""
    lines = [
        f"  {'action':<32} {'folder':<9} {'declared':>9} {'firmware':>9} "
        f"{'wait':>9} {'ratio':>7}  completion{seen_head}",
        f"  {'-' * 32} {'-' * 9} {'-' * 9} {'-' * 9} {'-' * 9} {'-' * 7}  "
        f"{'-' * 18}{'-' * 7 if watched else ''}",
    ]
    for r in ordered:
        declared = f"{r.declared_ms / 1000:.2f}s" if r.declared_ms else "?"
        firmware = f"{r.firmware_ms / 1000:.2f}s" if r.firmware_ms is not None else "-"
        wait = f"{r.measured_ms / 1000:.2f}s" if r.measured_ms is not None else "-"
        ratio = f"x{r.ratio:.2f}" if r.ratio is not None else "-"
        seen = f" {str(r.confirmed or '-'):<6}" if watched else ""
        lines.append(
            f"  {r.name[:32]:<32} {r.folder:<9} {declared:>9} {firmware:>9} "
            f"{wait:>9} {ratio:>7}  {r.completion}{seen}"
        )
    lines += [
        "",
        f"  {summary['completed']}/{summary['actions']} completed by progress-100, "
        f"{summary['timeouts']} timeout(s), {summary['quiet']} quiet, "
        f"{summary['errors']} error(s)",
        f"  declared {summary['declared_ms'] / 1000:.1f}s, "
        f"firmware {summary['firmware_ms'] / 1000:.1f}s, "
        f"waited {summary['measured_ms'] / 1000:.1f}s",
    ]
    if summary.get("unstarted"):
        lines.append(f"  {summary['unstarted']} never reported running - their "
                     f"numbers describe nothing the robot did")
    if watched:
        lines.append(f"  watched: {summary['confirmed_yes']} yes, "
                     f"{summary['confirmed_no']} no, "
                     f"{summary['unconfirmed']} not judged")
    if summary["ratio_median"] is not None:
        lines.append(f"  firm/declared ratio: median {summary['ratio_median']:.2f}, "
                     f"min {summary['ratio_min']:.2f}, max {summary['ratio_max']:.2f}")
    return "\n".join(lines)


def write_json(path: str | Path, results: list[Row], *,
               summary: dict | None = None, meta: dict | None = None) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "robot.json declared durations vs measured progress-100",
        "note": "firmware_ms is write->progress-100; measured_ms is write->wait "
                "returned and includes polling latency",
        **(meta or {}),
        "summary": summary or summarise(results),
        "rows": [r.to_dict() for r in results],
    }
    out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return out


CSV_FIELDS = [
    "name", "path", "folder", "source", "declared_ms", "firmware_ms",
    "measured_ms", "ratio", "completion", "progress", "started", "confirmed",
    "ok", "error", "completed",
]


def write_csv(path: str | Path, results: list[Row]) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for row in results:
            data = row.to_dict()
            writer.writerow({k: data.get(k) for k in CSV_FIELDS})
    return out


ROW_FIELDS = [f.name for f in dataclasses.fields(Row)]


def load_json(path: str | Path) -> tuple[list[Row], dict]:
    """Read a run artifact back into rows, so it can be annotated later."""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = [Row(**{k: r.get(k) for k in ROW_FIELDS}) for r in payload.get("rows", [])]
    return rows, payload


def mark(path: str | Path, verdicts: dict[str, str],
         csv_path: str | Path | None = None) -> list[Row]:
    """Record a human's verdict on a completed run, in place.

    BLE cannot confirm that a stunt was performed: a dropped `action` frame and
    a finished one both end in a released queue. A person watching can tell the
    difference, so their answer is stored next to the numbers instead of being
    remembered in a conversation.

    `verdicts` maps an action name (or a fragment of its path) to `yes`, `no` or
    `unsure`. Names are matched on letters and digits only, so the card's casing
    does not have to be reproduced exactly.
    """
    rows, payload = load_json(path)
    wanted = {norm(k): v for k, v in verdicts.items()}
    for row in rows:
        for key, verdict in wanted.items():
            if key == norm(row.name) or key in norm(row.path):
                row.confirmed = verdict
    payload["rows"] = [r.to_dict() for r in rows]
    payload["summary"] = summarise(rows)
    payload["annotated"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    Path(path).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    if csv_path:
        write_csv(csv_path, rows)
    return rows
