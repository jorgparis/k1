# k1lib — drive your Robosen Interstellar Scout K1 from Python

Bluetooth LE control for the K1. No Wi-Fi on this robot, and the phone app is the
only "official" interface — this replaces it with something scriptable.

Built on the protocol reverse-engineered by
[RobosenJS](https://github.com/oklemenz/RobosenJS) (Apache-2.0). The joint table,
stunt list and command codes are read **live from that package's `robot.json`**,
so they cannot drift out of sync.

Source: **https://github.com/jorgparis/k1** (MIT). The public copy is sanitised
(robot serial, MAC and personal paths removed); `build/` stays out of git.

## Architecture

The shape borrowed from [Microduck](https://github.com/pollen-robotics/microduck):
a small always-on core that owns the robot and exposes *intents*, with swappable
transports underneath. Anything above the core says "do this thing" and never
sees a packet.

```
        k1ctl.py          scripts          LLM planner (quackd-style)
            \                 |                    /
             \                |                   /
              +---------------+------------------+
                              |
                       JSON-RPC 2.0            k1lib/wire.py
                              |                 (stdio / socket)
                        +-----+------+
                        |  RobotCore  |  k1lib/core.py
                        | all robot   |  owns state, clocks actions,
                        | state       |  validates intents
                        +-----+------+
                              |  Bus interface  k1lib/bus.py
                 +------------+-------------+
                 |                          |
            SimBus                     BleBus
      validates frames,             bleak, lazy-imported,
      tracks battery,               service 0000ffe0 /
      times stunts,                 characteristic 0000ffe1
      records sessions
```

Consequences worth having:

- **Nothing plugs in to develop a routine.** `--sim` runs the identical intent
  stream against `SimBus`, which rejects malformed frames, times every stunt for
  real, and answers state queries. A routine that runs clean in the simulator is
  sending well-formed frames.
- **The core has no BLE knowledge.** Adding a gamepad, a web UI, or a voice
  front-end means writing intents, not packets.
- **It runs as a daemon.** `k1ctl.py daemon` serves JSON-RPC over stdio, so a
  planner in another process (or another language) drives the robot without
  knowing anything about BLE.

### Files

| file | role |
| --- | --- |
| `k1lib/spec.py` | loads `robot.json`; joints, limits, actions, UUIDs |
| `k1lib/protocol.py` | packet encode/decode, checksum, joint struct, clamping |
| `k1lib/bus.py` | `Bus` interface, `SimBus`, `BleBus`, `scan_once` |
| `k1lib/core.py` | `RobotCore` — all robot state, all intents, event emission |
| `k1lib/wire.py` | JSON-RPC 2.0 framing; `serve_stdio`, `StreamClient/Server` |
| `k1lib/robot.py` | `K1` — thin friendly facade over `RobotCore` |
| `k1lib/sdcard.py` | read-only SD card inventory and stunt listing |
| `k1lib/bench.py` | play every action and measure its real duration |
| `k1ctl.py` | CLI |
| `tools/probe.py` | passive first-contact probe: connect, enumerate, ask state, never move |
| `tools/story.py` | build the story tables (First Boot, Derelict, Grimm, Le Chat Botté, Les Musiciens de Brême): speech, SFX, routine, preview |
| `tools/phone.py` | build the standalone phone player from the same routines |
| `routines/*.json` | routine files |
| `tests/` | ten suites, none of which need hardware |

## Setup (Windows, one time)

```powershell
winget install Python.Python.3.12
py -m pip install bleak
```

`bleak` is only needed for real hardware — every test and `--sim` run works
without it, because `BleBus` imports it lazily.

The `robosen-js` package supplies the spec, so keep it installed:

```powershell
npm install robosen-js
```

## Use

```powershell
python k1ctl.py scan                 # find the robot
python k1ctl.py info                 # battery + state
python k1ctl.py actions              # all 68 stunts and dances
python k1ctl.py joints               # joint limits and neutrals
python k1ctl.py do "Left Punch" "Cyberpunk"
python k1ctl.py walk forward --ms 1500
python k1ctl.py head 40
python k1ctl.py routine routines/hello.json
python k1ctl.py bench --only "Left Punch"   # time actions against their clocks
python k1ctl.py verdict build/bench/x.json --set "Left Punch=yes"  # what you saw
python k1ctl.py encode head=160      # print bytes without connecting
```

Add `--sim` to any robot command to run it against the simulator instead:

```powershell
python k1ctl.py --sim routine routines/hello.json
python k1ctl.py --sim record routines/hello.json session.json
```

`record` runs a routine on the simulator and writes every frame it produced to a
JSON file — useful for diffing a routine edit, or for a firmware bug report.

**Before any command:** power the K1 on and **close the K One app**. BLE permits
only one central connection — if the app is connected, the scan finds nothing.

## Library

```python
import asyncio
from k1lib.robot import K1

async def main():
    async with K1() as k1:            # real robot
        await k1.say(101)
        await k1.action("Bow, Please")
        await k1.pose({"head": 160}, speed=30)
        await k1.pose_delta({"leftArm": -40})
        await k1.move("forward", ms=800)

asyncio.run(main())
```

Swap `"ble"` for `"sim"` and the same script runs with nothing plugged in:

```python
async with K1("sim") as k1:
    ...
```

To listen to what the robot reports back:

```python
k1.on(lambda event, data: print(event, data))
# action   {'name': 'Left Punch', 'duration_ms': 800}
# progress {'action': 'Left Punch', 'percent': 50}
# state    {'battery': 87, 'volume': 40, ...}
```

## Routines

JSON step lists. Run them with `k1ctl.py routine <file>`.

```json
[
  { "say": 101 },
  { "speak": "02/101" },
  { "action": "Kung Fu" },
  { "actionPath": "SpeAction/showtime" },
  { "glide": { "head": 90 }, "maxStep": 3 },
  { "pose": { "head": 90 }, "speed": 30 },
  { "delta": { "rightArm": 40 } },
  { "move": "turn right", "ms": 600 },
  { "wait": 1.0 }
]
```

Step keys are friendly (`action`, `move`, `say`); they are mapped explicitly to
intent parameters in `core.STEP_INTENTS`, so a mismatch is a clear error rather
than a `TypeError` halfway through a routine.

`routines/showcase.json` uses every key at least once and is the quickest way to
see the whole vocabulary in one run:

```powershell
python k1ctl.py --sim routine routines/showcase.json
python k1ctl.py --sim record routines/showcase.json frames.json   # every frame
```

`speak` is how custom audio joins a routine — it takes an on-device path, so the
words have to be copied to `02/<number>.mp3` over USB-C first. `actionPath`
reaches the 13 card-only stunts `robot.json` never names, and any `.sh` you add
yourself: `routines/look.json` plays `SpeAction/look left` and
`SpeAction/look right`, two silent moves derived from `shake head` (below).
`glide` eases to a pose
in steps of at most `maxStep` counts instead of one 17-joint jump, and `fast`
drops the inter-frame delay for dry runs.

### A custom story, generated end to end

`tools/story.py` builds narration, sound effects, the routine, and an audio
preview from one table — so the clips, the ids and the timing cannot drift apart:

```powershell
py tools/story.py                     # First Boot: build + preview, no robot
py tools/story.py --copy D:\          # then copy build/story/02/*.mp3 to D:\02

py tools/story.py --story derelict    # Derelict: 164 s science-fiction story
py tools/story.py --story derelict --copy D:\   # copies 401-418 to D:\02

py tools/story.py --story derelict_fr # the same film, voiced in French
py tools/story.py --story derelict_fr --copy D:\  # copies 501-518 to D:\02

py tools/story.py --story grimm       # Le Pêcheur et sa Femme, three voices
py tools/story.py --story grimm --copy D:\        # copies 601-619 to D:\02

py tools/story.py --story chat_botte  # Le Chat Botté, three voices + ogre
py tools/story.py --story chat_botte --copy D:\   # copies 701-734 to D:\02

py tools/story.py --story bremen      # Les Musiciens de Brême, four animals
py tools/story.py --story bremen --copy D:\       # copies 801-838 to D:\02
```

```powershell
python k1ctl.py --sim routine routines/story_first_boot.json   # validate frames
python k1ctl.py --sim routine routines/story_derelict.json
python k1ctl.py --sim routine routines/story_derelict_fr.json
python k1ctl.py --sim routine routines/story_grimm.json
python k1ctl.py --sim routine routines/story_chat_botte.json
python k1ctl.py --sim routine routines/story_bremen.json
```

Each story owns a disjoint clip-id range and its own output directory — First
Boot is `301+` in `build/story/`, Derelict is `401+` in
`build/story/derelict/`, the French Derelict is `501+` in
`build/story/derelict_fr/`, the Grimm tale is `601+` in
`build/story/grimm/`, *Le Chat Botté* is `701+` in
`build/story/chat_botte/`, and *Les Musiciens de Brême* is `801+` in
`build/story/bremen/` — because the card's `02/` folder is flat. Building
one therefore cannot overwrite the clips another routine names; a test asserts
the ranges never intersect. Derelict uses only stunts the owner watched perform,
and `Play Dead` again supplies the "death" because it is the one factory action
that lies down and stands itself back up.

The French version is a translation rather than a transliteration — the jokes
are rewritten where a literal line would land flat — and it uses the installed
`Microsoft Hortense Desktop` voice at rate 0. Its lengths are re-measured from
its own clips (174 s, 7 s longer than the English), which is exactly why the
generator measures instead of reusing the English timeline.

`--story grimm` is *Le Pêcheur et sa Femme*, a Brothers Grimm tale with a cast
rather than one narrator: Hortense narrates, Paul is the fisherman, Julie is his
wife, and the golden flounder is Hortense slowed and pitched down with a
low-pass and echo, so the fish reads as underwater. A story table line can name
its speaker (`("say", text, "wife")`), and the build reports which character
said it in `timeline.md` and the phone captions. The flounder is the only magic
in the tale; the 10 stunts are all factory moves the owner watched perform, and
there is no walking, because the K1 has no step action — `move` is a posture
change, not locomotion. One of those stunts is not in `robot.json` at all: the
fisherman's silent refusal is `("actionPath", "SpeAction/shake head")`, the
card's own head-shake script, measured at 4.47 s on the robot.

`--story chat_botte` is *Le Chat Botté*, Perrault's Puss in Boots, with the same
cast machinery: Hortense narrates, Paul is the miller's youngest son, Julie is
the cat (rate +4, pitch +8 so she sounds quick and pleased with herself), and
Hortense returns slowed and pitched down as the ogre (rate -18, pitch -28, a
1.5 kHz low-pass and 0.10 echo). The two ogre lines are the boast and the fatal
"Rien de plus facile"; the lion and the mouse are sound effects (`thud`,
`boing`), because the K1 has no transformation action. Clips `701–734`,
253.0 s, 11 stunts, routine `routines/story_chat_botte.json`; every stunt is a
factory action the owner watched perform, and there is again no walking.

`--story bremen` is *Les Musiciens de Brême*: the donkey is Paul slowed and
dropped (rate -12, pitch -20, 1.8 kHz low-pass), the dog is Paul near normal
(-2/-6), the cat is Julie at +2/+6, the rooster is Julie pushed high and fast
(+8/+16), and the robber is Hortense at -6/-12 behind a 2.2 kHz low-pass and
0.12 echo. Clips `801–838`, 253.5 s, 11 stunts, routine
`routines/story_bremen.json`. The tale needs no transformation effect — the
"monster" is the stack of animals — and the night intruder is the `boing`/`thud`
pair plus a rooster crow. All 11 stunts are confirmed `yes`, all end standing, no
walking.

A story beat may be `action` (a `robot.json` name) or `actionPath` (a raw card
path, for scripts the spec does not name). Both go through the same 6 s settle
rule and the same measured clocks: the build reads `build/bench/all68.json` for
the 68 declared actions and `build/bench/card_only.json` for the card-only ones,
so a `shake head` beat is paced on 4.47 s and not on the 2.0 s placeholder that
a spec-unknown path would otherwise get.

Three of those voices (Hortense, Julie, Paul) are **OneCore/WinRT voices**;
`System.Speech` can only see the two SAPI5 registrations and cannot select
Julie or Paul at all. The generator reaches them through
`Windows.Media.SpeechSynthesis` and SSML, which is also what supplies the pitch,
and resamples their 16 kHz output to the project's 44.1 kHz.

It uses the installed Windows voices for speech (no cloud, no API key),
synthesises its
own effects — no ffmpeg, no sox — and encodes MP3 with `lameenc` installed into
`.audio-libs` so the system Python stays untouched. Waits are computed from each
clip's **measured** length, which is why the routine is generated rather than
hand-written.

`build/story/preview.mp3` is the whole thing on a local timeline: if the story
works there, it works on the robot, because the routine is derived from the same
table. **`build/story/preview.html`** is the same preview as a page you can watch
— audio embedded, timeline blocks for narration / stunt / effect, a caption that
follows playback, click-to-seek. It opens straight from disk, no server.

The preview and the routine are checked against each other — every clip the
routine names is asserted to exist before the build reports success.

`build/story/timeline.md` is the same numbers as a table you can read without
opening the browser, with a column saying where each length came from: narration
and effects are **measured** from the encoded files, stunt lengths are the
clocks **measured on the robot** (`build/bench/all68.json`), and the settle
pauses are generated.

The story itself is adapted to what the hardware showed. `Play Dead` and
`Open Fire` are beats because their real clocks (16.78 s and 13.91 s, against
declarations of 1.68 s and 1.40 s) are worth using; the old "Lay Down, then
Push Ups, the only way up" ending is gone because every factory action stands
the robot back up by itself. `Lay Face Down` and the four card-less names are
deliberately unused. And the generator enforces the **6 s settle** from the
benchmark: if the narration between two stunts is shorter than that, it inserts
the missing silence, so no stunt frame follows another too closely.

### Playing a story from a phone, with no laptop

Android does not expose BLE to a Linux userland, so Termux cannot run
`k1ctl.py` — `bleak`, `noble` and BlueZ all need an HCI or D-Bus daemon that
Android keeps behind its Java API, and `termux-api` has no Bluetooth calls. What
Android *does* have is Chrome's Web Bluetooth. `tools/phone.py` packs the frame
codec, the six routines and their captions into one self-contained page:

```bash
python3 tools/phone.py
# phone player -> build/phone/k1-story.html  (66 KB)
# Windows path -> C:\Users\<you>\k1\build\phone\k1-story.html
```

It makes no network requests, embeds no audio (the narration still plays from
the SD card), and uses the same `routines/story_*.json` files `k1ctl.py` plays —
a test asserts the embedded steps are identical. Connect writes the `autoStand`
toggle (`ffff03110115`, the same frame `K1.connect()` sends), so the robot's own
posture management is on for a phone run exactly as it was for the run that was
approved. The runner reproduces the rules
that matter on the wire: one command in flight, wait for `progress-100`, ignore
an unprovoked 100 that arrived without a start packet, the generator's 6 s settle
as a backstop, and each action's `grace`. It also adds what a phone needs and a
terminal does not: large captions, a progress bar, a **Stop** button, screen
wake-lock, a diagnostics log of every frame, and a **Rehearse** mode that walks
the timeline with no robot at all.

To run it: copy `k1-story.html` to the phone (USB/MTP, email, Drive — anything),
open it in **Chrome on Android**, close the **K One** app, tap **Connect**, pick
`K1-…`, keep the tab in front, then tap Play. Chrome refuses Web Bluetooth
outside a secure context; a `file://` page usually counts as one, but if the
chooser will not open, serve the same file from the phone itself:

```bash
# Termux, in the folder holding k1-story.html
python -m http.server 8000
# Chrome -> http://localhost:8000/k1-story.html
```

A `localhost` origin is a secure context by definition. The page diagnoses this
itself: it shows whether `isSecureContext` and `navigator.bluetooth` exist, and
says so instead of failing silently.

Two hardware caveats, both visible in the page: K1 action paths are 24–34 bytes,
so a stack that refuses long writes without a response may need the **Force
write-with-response** checkbox (the GATT layer then fragments the same frame at
the MTU); and a stale Chrome tab or a background tab can throttle timers, which
is why the page holds a wake lock and warns when it loses focus. This path has
not been performed on hardware yet — the offline proof is 51 checks in
`tests/test_phone.py`, including a fake robot that streams progress, drops an
unprovoked 100, and rejects a no-response write to force the fallback.

### Durations: declared, now measured

Every action has a clock, and it is worth seeing all of them at once:

```powershell
python k1ctl.py durations                    # 68 actions, grouped, 1021.9 s total
python k1ctl.py durations --sort duration    # shortest first
python k1ctl.py durations --json build/spec/durations.json
```

Those numbers are the vendor's `duration_ms` values. **Nothing paces on them.**
A routine sends an action and then waits for the robot to report `progress-100`,
so a stunt that runs long delays the next line rather than being cut off. The
table answers "how much time should I plan for", not "how long will it take" —
the card's scripts store per-frame speed bytes rather than a total, so the
vendor numbers had nothing to check them against until the benchmark below.

They were cross-checked on the robot on 2026-10-31 (`bench`, all 68, 6 s settle):
**64/68 reported `progress-100`, ratio median 1.00, min 0.98.** Two actions have
wildly wrong clocks — `Open Fire` is declared at 1.40 s but runs **13.91 s**, and
`Play Dead` is declared at 1.68 s but runs **16.78 s**. The nine long `Action/`
music-and-dance tracks run about 1 % short, and four other `Action/` names have
no file on the card at all. See `build/bench/all68.json`. If you decode a card
script with `tools/shdecode.py`, pass `--duration-ms 13907` for `Open Fire` and
`--duration-ms 16781` for `Play Dead`: the decoder otherwise rescales to the
declared clock and
those two previews run 10× fast.

### Benchmark: the same table, measured

The declared clocks are only worth having if they can be checked. `bench` plays
each action over Bluetooth, one at a time, and records when `progress-100`
actually arrived:

```powershell
python k1ctl.py bench --dry-run              # the plan, no connection
python k1ctl.py bench --only "Left Punch,Right Punch,Play Dead,Open Fire"
python k1ctl.py bench --json build/bench/actions.json --csv build/bench/actions.csv
python k1ctl.py bench --card-root D:\        # also every .sh on the SD card
python k1ctl.py --sim bench --only punch     # plumbing check, no robot
```

Each row has two clocks, both measured from the frame being written:

| field | meaning |
| --- | --- |
| `firmware_ms` | write → the robot's `progress-100`. The number to compare with `robot.json`. |
| `measured_ms` | write → the wait returning. Includes the 50 ms poll, so it is an upper bound. |
| `declared_ms` | the vendor's `duration_ms`, or `null` for a card-only script. |
| `ratio` | `firmware_ms / declared_ms`; `1.00` means the vendor clock is right. |

**No neutral joint frame is ever sent between actions.** That is a hardware
finding, not a preference. The first live run sent `stand` between actions, which
writes the `robot.json` neutral: on this robot that leans right and forward,
drives `rightArm` to its documented minimum (34), and the robot announced a
stuck right shoulder. An action sent after that `jointMove` was also ignored by
the firmware — the benchmark then measured a 16 s `timeout`, not a punch. With
no stand, `Left Punch` and `Right Punch` reported `progress-100` at 687 ms and
703 ms. The benchmark therefore has no stand option at all; each action is left
in its own end pose and the firmware owns the stance.

**Every factory action finishes standing.** That is an owner observation from
watching all 68 (2026-10-31), and it is why the benchmark needs no recovery move
between entries: `Lay Down`, `Lay Face Down`, `Play Dead`, `Handstand` and the
flips may put the robot on the floor or on its hands in the middle, but the
firmware stands it back up before the action ends. Nothing has to follow them, so
the next frame can be sent straight after the settle pause. A `timeout` still
stops the run: the queue has been released while the robot may still be moving,
so the next number would describe a dropped frame rather than a stunt.
`--keep-going` overrides that when the later numbers do not matter.

**`progress-100` is not the end of the action, and it is not evidence on its
own.** Three hardware findings fixed that:

* **A completion needs a start.** The percent packet carries no action id, so a
  late `100` from the previous stunt used to land on the next wait and end it
  instantly. With no start packet, the core now ignores the `100` and keeps
  waiting: `Quick sneak` and `The Squat` — paths that are not on the card — each
  answered 100 within ~130 ms and used to be recorded as 0.1 s "completions".
  Their real answer is "never ran".
* **A start packet still is not proof of a stunt.** `Lay Face Down` streamed
  9 → 100 % over 12.67 s and the owner watching saw no movement at all. So the
  artifact keeps a `confirmed` column filled in by a person, and a timing plus a
  100 % is reported as "the firmware answered", never as "the stunt happened".
* **The next frame needs a settle pause.** With no pause, a five-action batch
  performed only the first one; each subsequent frame was silently dropped. The
  default `--gap` is therefore **6 s**, which the owner confirmed is long enough
  to see each action separately and after which every action performed. Do not
  lower it for a batch.
* **Silence is not completion once an action has started.** `Open Fire` streams
  fine-grained progress (8, 16, 25 … 83 %) for 8.4 s with gaps over a second,
  so any fixed silence window cut it off. `quiet` now only applies to an action
  that never reported running; otherwise the duration-aware deadline decides.

The wait is duration-aware. `_await_action` uses
`max(action_grace_s, duration_ms/1000 + action_slack_s)` rather than a fixed
10 s cap, because `Robot Talk` is declared at 81 s and `Cyberpunk` at 58 s — a
fixed cap would have released the queue mid-dance and then measured the queue,
not the robot. `--max-wait` supplies a per-call cap for a benchmark that must
not hang on a silent robot; `grace_s` is accepted by `action` and `actionPath`
for the same reason.

**Measured on K1-XXXX (2026-10-30/31, 6 s settle, no joint frames): 64 of 68
actions reported `progress-100`, ratio median 1.00, min 0.98.** A person watching
confirmed 63 of those 64 performed; the other 4 never ran. The vendor clocks
are right to within a few percent — the numbers land on 1/64 s ticks (15.6 ms),
so ±0.03 s is quantisation, not drift. The exceptions are worth knowing:

| finding | evidence |
| --- | --- |
| `Open Fire` is **×9.93** | declared 1.40 s, measured 13.91 s. The declared clock is simply wrong. |
| `Play Dead` is **×9.99** | declared 1.68 s, measured **16.78 s**. Also wrong, and the reason the first run timed out: the default deadline is `1.68 + 15 s`, which is 100 ms short of the real finish. Use `--max-wait 30` for this one. |
| 9 `Action/` music-and-dance tracks run ~1 % short | the nine tagged `music, dance` in `robot.json`, 53 s → 52.78 s, 61 s → 60.19 s, 64 s → 63.20 s, 81 s → 80.31 s; the shortfall grows with length, like an uncounted audio tail. |
| `Funny`, `Easy Action`, `Quick sneak`, `The Squat` do not exist on the card | no `.sh` at `Action/<name>`; the firmware answers 100 % in ~130 ms and nothing moves. |
| `Lay Face Down` reported 100 % and moved nothing | 12.67 s of 9 → 100 % progress; the owner saw no movement, having just played `Lay Down`. A full progress stream is still not proof. |

`Open Fire` and `Play Dead` are the only two vendor clocks that are wrong by a
large factor; every other action, including all the long music tracks, is within
2 %. Both were confirmed visually by the owner, so their measured clocks are real
stunts, not firmware traffic.

A verdict can name an action that contains a comma (`Bow, Please`,
`Kneel,Please`): a comma only separates entries when the piece before it carries
an `=`, and `;` is always a hard separator.

Artifacts: `build/bench/all68.json` / `.csv` (merged), and the per-group runs
next to them. A person's verdict belongs in the artifact, not in a chat:

```sh
python k1ctl.py verdict build/bench/all68.json --set "Left Punch=yes,Boogaloo=no"
```

`tests/test_bench.py` pins the filters, the card merge, the deadline, timeout
handling, the start-packet rule, the settle default, and the JSON/CSV
artifacts. Like every test it runs on `SimBus`, so it proves the plumbing, not
the timings. Real numbers only come from a Windows BLE run.

Two unknowns worth stating, since the card is only readable while tethered and
was not available when this was written: the clips are **44.1 kHz mono 64 kbps**
MP3, which is a guess at what the firmware likes (the factory clips' format is
unknown), and playback latency is uncompensated — a 0.35 s pad is added after
every line. If narration sounds clipped or late on the robot, those are the two
knobs.

## Daemon mode

```powershell
python k1ctl.py --sim daemon
{"jsonrpc":"2.0","id":1,"method":"methods"}
{"jsonrpc":"2.0","id":1,"result":{"methods":["action","autoOff", ...]}}
```

Intents: `action`, `autoOff`, `autoStand`, `delta`, `head`, `move`, `pose`,
`readPose`, `routine`, `say`, `shutdown`, `stand`, `state`, `stop`, `volume`.
Plus `methods` for introspection.

Requests are handled concurrently and replies are matched by `id`, so a slow
action never blocks unrelated calls. Progress arrives as server-initiated
notifications (messages with no `id`). On stdin EOF the server drains in-flight
requests rather than cancelling them, so a client that writes everything then
waits still gets every reply.

Note that RPC `params` use intent parameter names (`name`, `direction`,
`clip`), which differ from routine-file keys (`action`, `move`, `say`):

```
{"jsonrpc":"2.0","id":1,"method":"action","params":{"name":"Left Punch"}}
{"jsonrpc":"2.0","id":1,"method":"move","params":{"direction":"forward","ms":800}}
```

## Protocol

```
+--------+---------+--------+---------+----------+
| header | numByte | type   | data... | checksum |
| ff ff  |  1 byte | 1 byte |  0..n   |  1 byte  |
+--------+---------+--------+---------+----------+
```

`numBytes = 1 + len(data) + 1`, `checksum = sum(body) % 256`.

Service `0000ffe0-…`, characteristic `0000ffe1-…`, writes without response.

- 17 addressable joints (indices 0–16), fixed filler 17–23, speed at 24
- 68 canned actions: `Action/<name>` (dances), `ProAction/<name>` (stunts)
- audio clips `AppSysMS/101–111`, joint clips `201–216`

A joint packet can never be built empty. `packet(code("jointMove"))` would mean
an all-zero pose, so it raises instead of sending — `joint_struct()` must be
passed explicitly.

## Tests

```bash
python tests/run_all.py          # everything, no hardware needed
```

| suite | what it proves |
| --- | --- |
| `test_vectors.py` | 12 golden frames match the JS reference byte-for-byte |
| `test_protocol.py` | spec coherence, struct round-trip, checksum rejects every single-bit flip and every truncation |
| `test_architecture.py` | the same intents work on any bus; the sim rejects malformed frames; clamping reaches the wire; bad input never crashes the core |
| `test_wire.py` | daemon over a real socket: id matching, notifications, concurrent calls, clean shutdown |
| `test_daemon.py` | the daemon as a subprocess over stdio, including drain-on-EOF and junk input |
| `smoke.py` | dry-run a routine and print every frame it would send |
| `test_durations.py` | the declared-duration table is complete and honest about being a declaration |
| `test_bench.py` | benchmark filters, card merge, duration-aware deadline, timeout handling, JSON/CSV |
| `test_phone.py` | the phone page is self-contained, embeds the same routines, and its JS codec matches Python byte-for-byte; a fake robot exercises progress, write fallback, rehearsal and Stop |

`test_protocol.py` matters because the spec is read live from `node_modules` — if
that package changes underneath, these are the tests that notice before the robot
does.

## Verified on hardware

Confirmed against a real K1 (`K1-XXXX`, address redacted, bleak 3.0.2
on Windows):

| claim | result |
| --- | --- |
| advertises `K1-<serial>`, contains `K1` | ✅ so the default `--name K1` matches |
| service `ffe0` / characteristic `ffe1` present | ✅ exactly as the spec says |
| `write_gatt_char` with `response=False` | ✅ |
| state request → notification | ✅ checksum valid, `{"battery": 102, "volume": 90, ...}` |
| full 25-byte `jointMove` struct | ✅ robot moved to it and held the pose |
| `robot.json` neutral values | ⚠️ accepted and held, but **the robot leans right/front at them**; they drive `rightArm` to its minimum (34), and the robot reported a stuck right shoulder after standing there |
| `jointSync` (`e9`) | ❌ **accepted but never answered** |
| `action` → progress notification | ✅ `Left Punch` reported 50% then 100% |
| `autoStand` toggle (`11`) | ✅ state reports `1` once posture management engages |
| `kind` / `version` / `date` (`f6`/`f7`/`f8`) | ✅ `K1`, `VER:3.04L`, `SH2022-08-15` |
| `audio` clip | ✅ clip 101 played audibly |
| `volume` (`0d`) | ✅ 90 → 55 → 90, confirmed by state readback each time |
| BLE frame recording | ✅ every outbound frame captured on the real bus |
| `move` (`01`–`08`) → `stop` | ✅ frame pair correct, 516ms apart for a 500ms move; no ack, as `receiveKind: none` says |
| `move` posture effect | ⚠️ **lowers the body into a locomotion stance** |
| command queue, back-to-back actions | ✅ two stunts sent with no wait both played, in order, each with its own 50%/100% |
| daemon (`--bus ble`) over stdio | ✅ JSON-RPC in, replies + progress notifications out, drain-on-EOF works |
| `action` with a spec-unknown name | ✅ `ProAction/ZZTestPunch` — a copy we added ourselves — played, confirmed by eye |
| `SpeAction/Resume Standing` | ⚠️ reachable and accepted, reports `progress-100`, **but does not get the robot up** |
| `Push Ups` as floor recovery | ✅ **from lying horizontally: push-ups, ended standing** |

Five findings that change how you write routines:

**Joint positions cannot be read back.** The firmware accepts `jointSync` and
returns nothing, so `readPose` can only ever report the positions *you*
commanded. The core tracks those locally, and `readPose` now says so plainly
rather than passing off its own cache as a readback — see the "faking data" note
below. The simulator deliberately ignores `jointSync` too, so it stays no more
capable than the real thing.

**`robot.json`'s neutral is not a stable stance.** This is the most important
thing to know before you send a single joint command. Commanding every joint to
its documented neutral — what `stand` does — leaves the robot **leaning
right and forward**. It holds the pose, it doesn't fall, but it is visibly off
balance and that's exactly the state you don't want before a stunt. The right
side is the sharp end of it: `rightArm` is pinned to its documented minimum (34)
and `rightShoulder` sits at 36 of a 6–226 range. On 2026-10-30 the robot held
that frame and then announced a stuck right shoulder; it freed by hand, but the
frame is now treated as unsafe. The robot's genuinely stable stance is the one
its own firmware uses after an action, and those joint values are not knowable,
because readback doesn't exist.

So a `jointMove` on this robot is *not* a small, safe thing. Treat it with the
same suspicion as a stunt: pillows down, and prefer `action`/`move` over poses.

**A `move` is a posture change, not a translation.** A 500ms "turn left" does
not rotate in place — observed on hardware, the robot **bends its knees and
drops into a running position**. So the movement commands need the same caution
as stunts, and they're why the pose goes stale. There is also no speed control
anywhere in the protocol: duration is the only knob, and the spec's floor is
500ms.

**A pose command always moves all 17 joints.** The struct is fixed-width, so
`pose({"head": 150})` sends every joint — unmentioned ones at their current
commanded value. Observed twice on hardware: a head-only command after a stunt
made the robot lean, and a `stand` made it lean too.

Because an action leaves the robot in a stance we can't read, `RobotCore` now
tracks *staleness*. After any `action` or `move`, the remembered joint values no
longer describe the hardware, so the next `pose`/`delta`/`head` is refused:

```python
await k1.action("Left Punch")
await k1.head(20)              # IntentError: tracked pose is stale
await k1.head(20, assume_stale_ok=True)   # drags the body to remembered values
```

Refused calls emit no frames. A **forced** `stand`
(`allow_leaning_neutral=True`) clears the flag, but per the finding above it is
itself a movement into a leaning pose — and it is now refused by default. The
honest workflow is: let the robot own its stance (`autoStand`), and use `pose`
only when you accept a deliberate reposition.

**One command at a time, or the feedback degrades.** This is the single most
important rule, and it was learned by breaking it. The robot runs one action at
a time and reports progress faithfully *as long as you don't interfere*. Driven
serially, `Left Punch` gives a clean 50% then 100%, every time. But send anything
else mid-stunt and the feedback falls apart:

- a `state` poll during an action **suppresses that action's progress packets**
- a second action sent while the first is animating is **silently dropped** — a
  punch went missing this way during testing, and no error was raised

So the library enforces the discipline rather than trusting you to remember it:
`state` requests are deferred until any running stunt finishes, and a routine
**stops** rather than queueing a second action into a robot that never confirmed
it finished. `i_action` also reports which signal ended the wait
(`progress-100`, `quiet`, `timeout`) so completion is never a silent guess.

**`autoStand` reports late.** Writing the toggle leaves the state field reading
`0` for a while; it flipped to `1` on a later connect, after posture management
had actually engaged. Don't treat a `0` immediately after the write as "the
toggle was rejected" — and equally, don't assume fall prevention is active until
you see the `1`. The official app writes this on connect for that reason.

### In the spec but deliberately not used

`robot.json` contains a few commands this library implements no intent for:

| command | code | why not |
| --- | --- | --- |
| `jointLock`, `jointLockOne`, `jointLockAll`, `jointUnlockAll` | `ed`–`eb` | servo locking. `jointLockAll` would let a "head-only" write genuinely be head-only, which would fix the biggest limitation above — but locking joints the robot is standing on is exactly how you get a fall, and there is no readback to confirm a lock took. Not worth the risk without supervision. |
| `transform` | `0a` | undocumented, no parameters, no description. Unknown semantics on a robot that cannot catch itself. |
| `shutdown` / `done` / `failure` | `fa` / `ff` | `shutdown` is wired (`i_shutdown`) but obviously not run by anything routine. |

### Before anything moves

`K1.connect()` enables `autoStand`. Use `--no-auto-stand` for read-only work:

```powershell
python k1ctl.py --no-auto-stand info
python k1ctl.py --no-auto-stand readpose
```

And run the passive probe first if you ever want to re-check what the robot
exposes — it connects, enumerates, asks for state, and never commands a joint:

```powershell
python tools/probe.py --name K1
```

### Fall safety

**Factory actions are safe.** All 130 `.sh` scripts on the card are
vendor-authored trajectories, written and factory-tested for this robot and its
balance controller. Any of them can be played. Blocking them on a keyword match
was second-guessing the manufacturer, and hardware proved it wrong twice:
`Push Ups` and `Left Kick` are both flagged by the classifier and both have been
watched performing cleanly.

```powershell
python k1ctl.py do "Handstand"              # runs
python k1ctl.py do "Handstand" --strict     # refused: name-matched risk
python k1ctl.py safe                        # what the classifier thinks, and why
```

What that does *not* mean is that every action stays upright while it runs.
`Lay Down`, `Handstand` and the flips go to the floor or onto their hands in the
middle of the move — that is outcome, not risk — but **every one of them stands
the robot back up before it ends** (owner, watching all 68, 2026-10-31). So no
factory action leaves the robot needing a recovery move.

What is **not** factory is our own joint writing — `pose`, `glide`, `stand`,
`head`, `delta`. Those get no fall-risk block at all, because the real hazard
there is different and is guarded separately: the stale-pose check refuses joint
writes after an action, `glide` exists so no joint moves more than `max_step`
counts in a frame, and `stand` itself refuses to write the leaning vendor neutral
unless explicitly forced. That is where the caution belongs.

The classifier is still computed and still reported on every action result as
`fall_risk`, so the guess is visible — it just isn't a veto. `--strict` restores
the veto for scripts that want it, and routine files can set `"strict": true`
per step.

### Verified on hardware

Factory actions are safe to play — that is the rule, not a finding. This table is
the record of what has actually been watched on this robot, kept as provenance
rather than as an allow-list:

| action | observation |
| --- | --- |
| `Left Punch` | clean, upright — repeatedly, including queued |
| `Right Punch` | clean, upright |
| `Push Ups` | from lying horizontally: push-ups, ended standing — **the floor recovery** |
| `Left Kick` | kicked cleanly, stayed upright |

The list is short because it is a record of observation, not a statement about
risk. Everything else on the card is equally vendor-authored.

**The one thing worth knowing is outcome, not safety.** Some factory actions put
the robot on the floor or on its hands *by design* — `Lay Down`, `Lay Face Down`,
`Take A Seat`, `Handstand`, `Front Flip`, `Back Flip` — but all of them stand it
back up before they end (owner, 2026-10-31). None is a failure state and none
needs a recovery move: a `Lay Down` in the middle of a routine is just the move
you asked for, and the next action starts from standing.

### The command queue

The robot runs one command at a time, and it degrades if you don't respect that.
This isn't a guideline the library asks you to follow — it's an invariant it
enforces. Exactly one command is in flight; everything else waits its turn in
arrival order.

```python
await asyncio.gather(
    k1.core.run("action", {"name": "Left Punch"}),
    k1.core.run("action", {"name": "Right Punch"}),
    k1.core.run("state", {}),
)
# all three complete; the poll does not corrupt the stunts, and neither
# action is dropped
```

- **`i_action` holds the queue for the whole animation**, not just the write.
- **`i_action(wait=False)` returns immediately but keeps holding the queue**
  until the robot genuinely finishes — otherwise "don't wait" would mean "let
  the next action cut in", which is the bug this exists to prevent.
- **`i_move` holds it** from the first frame to the `stop`.
- **`i_state` just queues** behind whatever is running. `during_action="cached"`
  reads the last known state without touching the radio; `"force"` opts out.
- **Shutdown drains the queue** before closing, so the robot is never left
  mid-stunt.
- `core.busy` and `core.queued` let a client see the queue instead of guessing.

`i_action` reports **which signal** ended its wait — `progress-100`, `quiet` or
`timeout` — so completion is never a silent guess. A routine stops on `timeout`
rather than firing the next action into a robot that never confirmed it finished.
The deadline is the action's own declared clock plus slack (or the 10 s grace
floor, whichever is larger), so an 81 s dance is waited out instead of being cut
off; `grace_s` overrides it per call.

One subtlety worth knowing if you touch this code: the progress signal is
cleared **inside** the lock, not before acquiring it. Clearing it early lets a
queued action inherit the previous action's `set()` and report instant, fictional
success. That bug shipped briefly and is now pinned by a test.

### Making it talk

The honest shape of this, because it decides the whole approach: **the robot has
no text-to-speech.** The `audio` command takes a *path* to a clip that already
exists on the robot, never words of your own. Nothing in the protocol uploads a
file, so generated speech has to get onto the robot by another route before it
can be played.

What the spec declares is two ranges of built-in clips, expanded from
`robot.json` rather than hand-copied:

| folder | group | ids |
| --- | --- | --- |
| `AppSysMS` | `program` | 101–111 |
| `AppSysMS` | `joint` | 201–216 |

```python
await k1.core.i_audio()   # what the robot says it has
await k1.core.i_folders() # folders, and clips grouped under each
```

`audioNames` and `folderNames` are **list queries**: the robot replies with a
stream of notifications terminated by `done` (`fa`). This matters because
`robot.json` is a build-time document — it knows what the firmware shipped with,
not what the K One app has downloaded onto the robot since. Only the robot knows
that, and `not_in_spec` is where that difference shows up.

> `list_complete` is reported explicitly. A stream can deliver every item and
> still never send `done` — a full-looking list that might be truncated. Never
> present that as complete.

### Playing your own audio

Read off the robot's SD card over USB-C, so this is recorded knowledge, not
something `robot.json` can tell you — it only ever mentions `AppSysMS`:

| folder | contents | notes |
| --- | --- | --- |
| `02/` | *(empty)* | **user-programmed audio goes here** |
| `AppSysMS/` | 28 × `.mp3`, `101`–`111`, `200`–`216` | built-in clips; **do not modify** |
| `AppProMS/` | 10 × named `.mp3` ("Delight", "Funky Jam", …) | music beds for stunts |
| `SysMS/` | 16 × `.mp3` ("Charging", "Sleep", "Batlow", …) | system sounds |
| `WarnSysMS/` | 38 × `.mp3`, `066`–`159` | warning clips; **do not modify** |
| `00/`, `01/`, `11/` | 8, 9 and 82 × `.mp3` | audio tracks; **do not delete** |
| `ProAction/`, `SpeAction/`, `Action/` | 55 / 66 / 9 × `.sh` | stunt choreography, **binary** |
| `SHR0/`, `SysCF/`, `SysOS/` | 24-byte blobs, tiny config | **do not modify** |

Two facts that make the path scheme work, both confirmed by ear on hardware:

- **The firmware appends the extension.** The spec sends `AppSysMS/101`; the file
  is `AppSysMS/101.mp3`. Confirmed: sending `02/101` plays `02/101.mp3`.
- **`02` is the sanctioned user folder**, and it is the one folder that will not
  resolve a lettered filename.

### Verified audio matrix

Each row changed exactly one variable, and each was confirmed by ear:

| sent | file on card | result |
| --- | --- | --- |
| `AppSysMS/101` | `AppSysMS/101.mp3` | ✅ plays |
| `02/101` | `02/101.mp3` | ✅ **plays — the user folder works** |
| `02/story01` | `02/story01.mp3` | ❌ **silent** |
| `AppProMS/Delight` | `AppProMS/Delight.mp3` | ✅ plays |

Letters are **not** the problem — `AppProMS/Delight` plays. `02/` specifically
won't resolve a lettered name, while system folders do. So the rule to program
against is **numeric ids in `02/`**. `speak` warns when you break it, but still
sends the frame, because we know the boundary rather than the mechanism.

This is the whole answer to "can we make it talk": generate speech, copy it to
`02/<number>.mp3` over USB-C, and `speak("02/<number>")`.

### The list queries come back empty

`audioNames` and `folderNames` are real, working queries — the streams arrive and
terminate with `done` — but on real hardware they return **nothing**. So the
robot cannot tell us what audio it holds, `folder_confirmed` is always `None` in
practice, and the SD card remains the only reliable inventory. Don't build
anything on those listings.

```python
await k1.say(205)                          # a built-in clip, by id
await k1.core.i_speak("02/101")            # your own audio, numeric id
```

`speak` checks only that the path is structurally boring — relative, no `..`, no
drive letters, no control characters — because that string goes to the firmware.
It reports `expects_file`, so you can see which file it is aiming at, and
`folder_confirmed` (`True`/`False`/`None`) so it never implies it knows a folder
exists. Aiming at a protected folder produces **advice, not a block** — we're
playing, not writing.

To look at the card yourself:

```powershell
# Windows exposes the robot as a removable FAT volume (D: for us, 475 MB)
explorer.exe D:\
```

### What's on the SD card that `robot.json` doesn't know

Plug the robot in over USB-C and Windows exposes the card as a removable FAT
volume (a 475 MB `D:` for us). `python k1ctl.py card` inventories it — **read-only,
by design**, since the card's own warning file marks most of it do-not-touch.

```powershell
python k1ctl.py card D:\
```

| | |
| --- | --- |
| stunt scripts on the card | **130** (9 `Action`, 55 `ProAction`, 66 `SpeAction`) |
| actions in `robot.json` | **68** |
| playable but unknown to the spec | **13**, all in `SpeAction` |
| stunts present in both `ProAction` and `SpeAction` | 53 |

Three things fall out of that, none of them guessable from the spec:

**`SpeAction/` is invisible to `robot.json`**, which only ever names `Action/`
and `ProAction/`. 53 of its 66 files duplicate a `ProAction` name — plausibly a
second difficulty tier — and the other 13 are unique:

> `Resume Standing` · `Chest Up` · `Open Arms` · `Open Legs` · `Raise Left Hand`
> · `Raise Right Hand` · `Raise Your Hands` · `lift your arms` ·
> `lift your left arm` · `lift your right arm` · `pose to take off` ·
> `shake head` · `showtime`

**And the firmware will play any of them.** Verified by copying
`ProAction/Left Punch.sh` to `ProAction/ZZTestPunch.sh` — a byte-identical copy
under a name the spec has never seen — and asking for it by path. The robot
punched. Confirmed by eye, not just by the progress packets.

So `robot.json` is not the limit of what the robot can do. **130 stunt scripts
exist on the card; the spec names 68.** `action_path` is how you reach the rest:

```python
await k1.core.i_action_path("ProAction/Left Punch")     # recognised, spec duration
await k1.core.i_action_path("SpeAction/Resume Standing")  # in_spec: False
```

The result says which case you hit (`in_spec`), and flags the duration as
estimated when the spec has no entry, because there is no spec clock for a name
it doesn't know.

**`Resume Standing` is reachable, and it does not get the robot up.** Tested on
hardware: it reported a clean 50% then 100% and nothing moved at all. The reason
is visible in the `.sh` files — keyframe count, where one record is 32 bytes:

| stunt | keyframes | |
| --- | --- | --- |
| `Resume Standing` | **2** | a small pose tweak |
| `Crouch Down` | 5 | |
| `Lay Face Down` | 11 | |
| `Lay Down` | 14 | |
| `Handstand` | 17 | |

Two keyframes cannot describe a 17-servo humanoid rising off the floor. The name
means "re-assert the standing pose" — recovering from a lean — not "get up".

**There *is* a floor recovery: `Push Ups`.** `SpeAction/Resume Standing` is not
it — that reported `progress-100` and did nothing. But **`Push Ups`
(12 keyframes) does work**: tested on hardware with the robot lying horizontally
on the floor, it performed push-ups and ended up standing.

```python
await k1.core.i_action("Push Ups")     # a factory action: allowed like any other
```

The classifier called this fall-risk because of the words "up" and the prone
motion — the same words that make it the recovery. That, and `Left Kick`, are why
the classifier is informational now instead of a veto: a factory action does not
need an override, because it is a factory action.

Caveats, honestly: the *recovery* is **one observation**, from lying horizontally.
A robot face-down may press into the floor instead of pushing off it, and nothing
has been done to establish reliability or battery floor (it worked at 37). Treat
it as *"try this first"* rather than *"this always works"*, and keep manual
repositioning as the backstop. That is a caveat about the recovery, not about
whether the action is safe to run.

**And progress reports 100% for actions that do nothing.** The `Resume Standing`
run is the proof: `completion: progress-100`, zero movement. So `completion`
tells you *how* the wait ended, never that the robot moved. It is a protocol
signal, not evidence of success — keep watching.

### The guard had a hole, and this found it

`fall_risk()` was a dict keyed by exact `robot.json` names, so `action_path`
routed straight around it: `front flip`, `back flip`, `left kick` and
`play drum` all exist on the card, none are in the spec, and all four sailed
through. The guard now classifies the string it is *given* rather than looking up
a precomputed list, so those are classified like anything else — and refused
under `--strict`. Caught by a test, not by inspection.

Two related fixes came out of the same work:

- **`"stand"` was removed from the risk words.** It matched exactly one spec
  action — `Handstand`, already caught by `"handstand"` — and it misclassified
  `Resume Standing`, the one stunt that helps the robot get up.
- **All 13 spec-unknown stunts are confirmed not fall-risk**, so nothing gentle
  was collateral damage.

### What the `.sh` format looks like

The record layout is decoded; the timing model is still an approximation.

- **Records are exactly 32 bytes.** All 130 factory files are exact multiples of
  32.

  | bytes | meaning |
  | --- | --- |
  | 0–2 | signed-magnitude values for torso joints the K1 does not expose (a larger Robosen model has them); they mirror with the body |
  | 3 | flags. **Bit 7 is the whole-record mirror/side bit**; the low bits carry a small sub-field |
  | 4–20 | the 17 servos, in `robot.json` index order — **`head` is byte 20** |
  | 21–27 | filler (the spec's fixed slots) |
  | 28 | per-frame speed, 1–100 |
  | 29 | relative emphasis, and the marker for a block a routine repeats |
  | 30–31 | tag: `0b xx` on the first record (`xx` = action id and audio key), `fc fc` normal, `fb fb` last |

- **Direction is one mirror bit, not a per-joint sign.** Servo slots hold travel
  from neutral, so `20` means "20 counts", never "left 20" or "right 20". Byte
  3's top bit flips the whole pose left/right: the factory's `Tilt Left` vs
  `Tilt Right`, `Raise Left Hand` vs `Raise Right Hand`, and `lift your left
  arm` vs `lift your right arm` all differ there. One bit is enough to turn the
  head either way — verified on hardware with the two moves below.

- **The first record's `0b xx` id selects the audio.** `0x3d` is `shake head`,
  which owns `11/061.mp3`. Changing only that id to one with no clip (`0x53`)
  keeps the trajectory and drops the voice — verified on hardware. The id is
  not a motion or safety selector.

- **Duration is not implied by record count.** `Do Squats` is 3 records for
  18000 ms; `Mechanical Beats` is 114 for 62000 ms. Timing lives inside the
  records and `shdecode.py` approximates it from speed and emphasis.

- **The `fb`/`fc` trailer is *not* a fall-risk flag.** It first looked like one —
  the `fb fb` files are things like `Crouch Down`, `Lay Down` and `back flip`.
  Checked against all 130: 10 fall-risk files end `fb` but 28 end `fc`, including
  every long dance. It is a coin flip, not a classifier.

### Two new moves: silent look left / look right

Both are edits of `SpeAction/shake head`; no joint value is invented.

| file | source records | byte 3 | head | audio id |
| --- | --- | --- | --- | --- |
| `SpeAction/look left.sh` | shake 0 + 1 | `80` | 0 → 20 | `0b 53` |
| `SpeAction/look right.sh` | shake 0 + 2 | `00` | 0 → 20 | `0b 53` |

Records 1 and 2 of `shake head` both target `head = 20`; they differ in the
mirror bit, which is exactly why the original is a shake. Keeping one of them
makes a one-way look, and the silent id removes the "shake head" voice. Play them
with `routines/look.json`:

```json
{ "actionPath": "SpeAction/look left",  "grace": 8 },
{ "actionPath": "SpeAction/look right", "grace": 8 }
```

Both were watched on hardware: left turned and held, right turned the other way
and held, both silent. The firmware serves newly added `SpeAction/*.sh` files by
path (proven first with a byte-identical copy of `shake head`). The card is only
writable over USB-C, and actions do not complete while tethered — copy, unplug,
then play.

The stunt choreography is still only partly mapped, so fall-risk stays a
name-based judgement — which is why `--allow-fall-risk` exists and why the block
is on by default.

## Limits worth knowing

- No Wi-Fi, no cloud, no firmware access — the STM32 is locked over SWD (see
  `boilerbots/flexo`). BLE is the only control path, so routines live on your PC.
- Neutral values come from the SDK spec. Query `read_pose()` before relying on
  them for anything aggressive.
- Voice control on the robot is unavailable while a BLE central is connected,
  which is the app's own documented behaviour.
- The battery field reads above 100 (saw 102), so don't treat it as a clamped
  percentage. Across one working session it fell **102 → 10**, and the robot then
  stopped advertising over BLE. Read it as a trend, nothing more.
- **A fallen robot can be recovered with `Push Ups`** — verified once, from lying
  horizontally: it pushed up and ended standing. A factory action, so it needs no
  override; what remains unproven is the recovery from other orientations.
  Manual repositioning stays the backstop.
- A neutral pose is *valid* but **not stable**: `stand` leaves the robot leaning
  right/front and has tripped a stuck-shoulder report, so it is refused by default
  (`allow_leaning_neutral=True` to force). Posture cannot be verified from
  telemetry — it needs eyes on the robot, every time.
- `packet(code("jointMove"))` cannot be built empty — it would mean an all-zero
  pose, so it raises instead. Pass `joint_struct()`.