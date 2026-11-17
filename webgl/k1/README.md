# K1

A raw WebGL model of the Robosen Interstellar Scout K1 with **all 17 servos
mapped on the model**, styled after an Atari XL / Amiga demo-scene intro. No
libraries, no build step, no modules.

The point is not the robot's looks, it is the mapping: each joint is named after
the servo that drives it, its index/neutral/travel come from the K1's own
`robot.json`, and a slider spans exactly the range that servo accepts. Whatever
the model does when you drag a slider is what that raw count would do to the
robot — *given the axis guess*, which is explained below.

## How to open it

**Double-click `k1.html`.** That is one self-contained file with all the CSS
and JavaScript inlined, so it runs from `file://` with no server and nothing to
miss. Click once to start.

If you prefer the readable version, open `index.html` instead — same demo, but it
loads the nine files in `src/`, so keep the folder together.

Either way, open the **`.html` file, not a `.js` file.** Opening `src/k1.js`
just shows the source text, which is not the demo.

If the files live in WSL, a Windows browser needs the `/mnt/c` path:

```
C:\path\to\echange\k1\k1.html
```

The WSL path (`/home/<you>/echange/...`) will not resolve in a Windows browser.
If you would rather serve it:

```sh
python3 -m http.server 8123 --bind 127.0.0.1     # then open 127.0.0.1:8123
```

**If anything does go wrong, the page says so** — and a warning never covers the
demo. A failure after the demo is already running shows a small red panel in
the bottom-right corner that fades after 14 s, or goes away on click or any
keypress. `k1.html` carries this guard with it, so a stray `favicon.ico` 404 can
no longer plaster an error over a working render.

## The 17 joints

| # | joint | neutral | travel | axis (guess) |
| --- | --- | --- | --- | --- |
| 0 | `leftThigh` | 129 | 29–229 | pitch |
| 1 | `leftCalf` | 60 | 10–220 | pitch |
| 2 | `leftAnkle` | 106 | 26–226 | pitch |
| 3 | `rightThigh` | 118 | 18–218 | pitch |
| 4 | `rightCalf` | 190 | 30–240 | pitch |
| 5 | `rightAnkle` | 146 | 26–226 | pitch |
| 6 | `leftShoulder` | 212 | 22–242 | pitch (fore/aft) |
| 7 | `rightShoulder` | 36 | 6–226 | pitch (fore/aft) |
| 8 | `leftHip` | 123 | 103–133 | roll |
| 9 | `leftFoot` | 123 | 93–133 | yaw |
| 10 | `rightHip` | 129 | 119–149 | roll |
| 11 | `rightFoot` | 115 | 105–145 | yaw |
| 12 | `leftArm` | 223 | 33–233 | roll (abduction) |
| 13 | `leftHand` | 116 | 16–216 | pitch (elbow) |
| 14 | `rightArm` | 34 | 34–224 | roll (abduction) |
| 15 | `rightHand` | 126 | 26–226 | pitch (elbow) |
| 16 | `head` | 122 | 42–202 | yaw |

Index, neutral, min and max are copied from `robot.json`, so a slider cannot
send a count the library would refuse. **The axis is now pinned by the card's
own actions; the direction is still a labelled guess**, because `robot.json` and
the `.sh` files both say how far a servo travels but not which way. Two things in
the data constrain the direction harder than a plain "make it look humanoid":

* **The pairs are mirror-mounted.** Every pair's neutrals sum to about 249
  (`leftShoulder 212 + rightShoulder 36 = 248`, the calves 60 + 190 = 250, the
  feet 238, the arms 257). Two copies of the same servo mounted as mirror
  images produce exactly that, so a symmetric pose needs **opposite raw deltas**
  on the pair: `left +d`, `right -d`. The `sign` column is written to match
  (`opposite` on pitch, `same` on roll/yaw — see the comment in `src/k1.js`).
* **A neutral at the end of travel means that joint is a one-way hinge.** The
  right arm sits exactly on its minimum, the shoulders 10 and 0 counts from
  their ends, the knees 50 counts from one end. The long remaining travel is
  the useful direction, so the signs make the knee and elbow *flex*, the
  shoulder *pitch forwards*, and the arm *abduct outwards*.

The axis assignment follows the card's own actions, which is stronger evidence
than "make it look humanoid": `Left Punch` travels the shoulder 100 counts and
reads as a horizontal punch, then travel on `hand` extends the arm (`say hello`
bends `hand` 70 and `arm` only 15); `Open Arms` needs `arm` to take the arm out
to the side. So:

* shoulder — **pitch** (z), the whole arm swings fore/aft
* arm — **abduction** (x), the whole arm swings out to the side
* hand — **elbow** (z), the forearm folds
* thigh/calf/ankle — pitch (z); the calf's sign is negative, because a knee
  folds the other way from a hip
* hip — **abduction** (x): `Open Legs` stores 20 on both hips, and almost every
  other action parks them at 10, so the short travel opens the stance; foot and
  head — yaw (small ranges)

If a real nudge on the robot proves a joint wrong, change its `axis`/`sign` in
the `JOINTS` table or its entry in `ACTION_DIR`, both at the top of `src/k1.js`,
and rebuild. Nothing else needs to know.

One global knob, `D.degPerCount = 0.90`, is how far one count turns a servo.
That is not a guess either — it is what the card's actions require. They treat
100 counts as a right angle: the shoulder's 100-count punch, `Open Arms` at
99/100 for arms straight out, `Do Squats` at 80 thigh / 90 calf / 30 ankle. At
0.90 the shoulders' 190-count travel is 171°, over the head and down the back,
and the calf's largest value in the corpus (160) is 144° of knee flex. At 0.40
every one of those is a shallow bow. Change it and every joint scales together.

## What's in it

* **One slider per servo** — the right-hand panel is built from `JOINTS`, so it
  cannot list a joint the model does not have. Rows are numbered by protocol
  index, an amber number means "not at neutral", and the selected row is cyan.
* **Presets** — neutral, look left, arms out, elbows, crouch, hip roll. Each is
  a mirror-symmetric pose inside every limit, written as `left +d / right -d`
  to exercise the mirror-mount convention. They show the mapping rather than
  pretend a factory stunt is modelled; the crouch is as deep as the knee travel
  allows (about 5 cm of display drop).
* **Every move the phone page has** — the right-hand `MOVES` browser is built
  from the same 90-entry menu as the K One phone page: 68 actions named by
  `robot.json` and 22 card-only scripts. Filter by name, pick one, press Play;
  the readout shows the clock, where it came from and how many keyframes are
  decoded. 86 have a `.sh` and play; the four `robot.json` names with no script
  (`Funny`, `Easy Action`, `Quick sneak`, `The Squat`) are listed and disabled
  rather than invented.
* **Push Ups** — one of those actions, decoded from the robot's own card
  (`ProAction/Push Ups.sh`), not invented. The file has 12 keyframes; its pump
  block is marked as the part a routine repeats, and the robot pumps three
  times, so the demo plays that block three times for 18 keyframes over the
  9.625 s measured on hardware: hands plant, the body goes flat on the floor,
  the arms pump the pelvis down and up three times while the legs stay straight,
  then it pushes back up and stands. See *Real actions from the card* below.
* **Copy pose** — writes the pose twice: an object by joint name, and the dense
  17-slot array by protocol index, which is the shape `jointMove` takes.
* **Record what the real robot does** — the BLE link carries no telemetry, so
  the page cannot know what the robot actually did. The `RECORD` strip is the
  manual bridge: play a move here, watch the robot, and press `V` (or type a
  note and press `Enter`) at each beat you can name. A mark keeps the action's
  own clock, your note, the pose on screen, and the model's lean and part
  heights at that second, so "the head touches here" can be compared with what
  the file asks for. `copy` puts the marks on the clipboard as one JSON block;
  nothing is uploaded. Clicking a mark seeks the take back to that second, and
  the strip folds away behind its header when the sliders need the room. A pose
  dialled by hand is remembered too: a take owns the sliders and would otherwise
  wipe it, so `U` (or `recall`) puts the last hand-made pose back with the second
  it was dialled against, ready to be marked.
* **Retro post chain** — everything renders into a 320×200 framebuffer that is
  then upscaled with `image-rendering: pixelated`. The post shader adds ordered
  Bayer 8×8 dithering, palette quantization, scanlines, an aperture mask,
  chromatic aberration, vignette and a beat flash.
* **1-bit HUD** — drawn on a 2D canvas, alpha-thresholded to one bit, composited
  in the post shader. Copper bars, marquee, VU meter.
* **Chiptune** — a small Paula-style sequencer at 150 BPM, **off by default**.
  The page is a pose rig, so `M` starts it explicitly; nothing auto-plays on
  the intro click or on a `#run` link.

## Controls

| Key | Action |
| --- | --- |
| `←` `→` | select a servo |
| `↑` `↓` | move it (hold `SHIFT` for a single count) |
| `0` | return the selected servo to neutral |
| `P` | play Push Ups (decoded from the card) |
| `SPACE` | pause / resume |
| `R` | reset the pose |
| `M` | music on / off (starts off) |
| `V` | mark a beat in the recorder |
| `U` | recall the last pose you dialled by hand |
| `W` | wireframe overlay |
| `N` | shading mode |
| `C` | colour cycle |
| `H` | HUD on / off |
| `K` | help |
| `1`–`4` | camera presets |
| `F` | fullscreen |
| drag | orbit · wheel: zoom |

Playing any action stops the idle camera orbit, because a slowly turning view
reads as the robot turning. A seated take (`Take A Seat`, `Sit`, `Stand`, the
seated looks and the hand show) also switches to preset `2`, the profile view:
a floor sit folds the legs straight forward, and from the front that fold
projects sideways and looks like a hip swing. A camera you set yourself is left
alone, so press `2` again if an earlier drag put you back on a front view.

A recorder mark is approximate on purpose: its clock is the page's, not the
robot's, so `V` stamps the second that was on screen when you pressed it. If the
robot has already finished, drag the bar under the move list to hold any second
and mark from there — it is a scrubber, and on a flip it reaches the settled
pose. If you dial the sliders to the robot's pose first, the mark stores that
pose as well and counts how many joints differ from the file; a joint edit
freezes the take instead of ending it, so the clock survives, and a head or
arm edit leaves the take's lean alone. Move a leg and the lean is re-solved
from the leg chain, because the legs are what decide which way the body tips.
Either way the dialled pose is re-seated vertically on the part carrying it —
the lowest body part, or the hands once Back Flip rolls onto them — so it
neither hangs above the floor nor sinks into it. A mark that recorded the head
and palms as a triangle is the exception: that still solves the root pitch that
levels all three contacts, so the head and both hands stay on the floor with
every dialled joint exact. For a flip
the mark also carries the file pose that produced its recorded lean: because
the page clock is approximate, opening the mark solves the root from the file
time whose pose matches that stored block, while the note keeps the second you
pressed. The owner's 3.461 s observation is additionally filed as a `markPose`
hint on Back Flip, covering the 3.35–3.55 s band the owner's approximate page
clock has actually read for that beat (3.404 s and 3.461 s), so scrubbing or
deep-linking anywhere in it shows the marked pose and its level root even on a
fresh load, without pasting the record first. That is why a mark made there with
no slider change still records the dialled pose and contacts rather than the
file's later 79° frame.
A mark with no pose of its own is just the model's pose
— it is not evidence that the robot agreed.

## Layout

`k1.html` is generated — edit the files below and re-run the bundler rather
than editing the bundle.

| File | Contents |
| --- | --- |
| `k1.html` | **generated** single-file build; this is the one to open |
| `index.html` | page shell, load-failure guard, script order, servo and record panel markup |
| `style.css` | retro shell, intro panel, servo panel, error panel |
| `src/math.js` | `M4` (compose is `T·Rx·Ry·Rz·S`), `V3`, scalar helpers |
| `src/geo.js` | flat-shaded geometry generators, merge, wireframe edge list |
| `src/gl.js` | program / mesh / FBO / canvas-texture helpers |
| `src/shaders.js` | sky, grid floor, planar shadow, scene, post |
| `src/k1.js` | the `JOINTS` table, 19-part rig, neutral pose, clamping, pose, `.sh` direction + sampler |
| `src/actions.js` | **generated** by `test/genactions.py` from the phone menu and the card's `.sh` files; display hints include `flip`, the observed `tripod` beat and Back Flip's head-planted `markPose` over the 3.35–3.55 s page-clock band; `window.K1ACTIONS` + `K1ACTION_LIST` |
| `src/hud.js` | 320×200 1-bit HUD |
| `src/music.js` | chiptune sequencer |
| `src/main.js` | GL setup, render passes, camera, servo panel, presets, action player, recorder, input |

## Real actions from the card

The robot's SD card stores each stunt as a `.sh` file of 32-byte keyframes. The
K1 Python library lives at `~/k1` (Windows: `C:\Users\<you>\k1`); its
`tools/shdecode.py` decodes one and `tools/phone.py` owns the 90-entry move
menu. `test/genactions.py` imports both and writes the whole `src/actions.js`:

```sh
python3 test/genactions.py                         # auto-detects the checkout when run inside it
python3 test/genactions.py --k1 /path/to/k1        # or point it at the k1 checkout
python3 /path/to/k1/tools/shdecode.py --list ~/K1Copy   # 131 scripts, 3 folders
```

The generator resolves each phone move to the `.sh` the phone would play
(`build/*/final` first for the derived look/wave/sit scripts, then
`K1Copy/ProAction`, `K1Copy/SpeAction`, `K1Copy/Action`), decodes it with the
clock the phone data carries, and marks where that clock came from. Four
`robot.json` actions have no script on the card (`Funny`, `Easy Action`,
`Quick sneak`, `The Squat`), so they are listed with no frames instead of being
silently dropped.

Nothing on the card documents the layout, so it was decoded from the bytes. A
record is `[4 bytes the K1 lacks][17 servo magnitudes][7 unused][speed][flags][tag]`,
and each servo byte is **how far to travel from neutral**, not a position — the
shoulder's 190 in `lift your arms` is exactly its 212−22 of travel, and the
largest calf value in the corpus (160) is exactly 220−60. The first record ends
`0b xx` (the action id), the last `fb fb`. Timing is not stored. Byte 28 is the
travel time for the move onto that keyframe, and it is not a speed: **a larger
value is a slower move** (`Left Punch` strikes on 25 and returns on 15). Byte 29
adds about 100 ms per unit, and the first record is a lead-in. Those two bytes,
not the joint distance, predict the robot's own record boundaries: across 150
clean boundaries the segment length and the joint change correlate at about
zero, while the medians of the boundaries form the `TRAVEL_MS` table and the
model reproduces each one-shot action's total to 98–99 %. `shdecode.py` uses
them as weights and rescales to the action's clock. Where the robot's own
`progress` trace has one sample per record, the demo uses the measured arrivals
themselves: `test/timings.json` holds the sanitised traces for the 32 one-shot
actions that have them, and `test/genactions.py` writes `timing: "trace"` for
those and `timing: "table"` for everything else. That clock is the one measured
on the robot where it exists (Push Ups 9.625 s, Play Dead 16.781 s, Open Fire
13.907 s), `robot.json`'s declaration for the four moves with no script, and a
record-count estimate (~700 ms per record, the median of the measured card-only
scripts) for the 14 card scripts nobody has timed. The browser says `measured`,
`declared` or `estimated` next to every action, so an estimate is never
presented as a measurement.

The flags byte (29) is an **emphasis** that does have a unit after all: each
point adds about 100 ms to the move that arrives at that keyframe. `Propose`'s
85-flag record arrives 9515 ms after the previous one, against the 1000 ms base
move for its speed-40 setting. The same value on the same pose therefore means
different extra seconds when the move onto that pose differs — the byte marks an
arrival, not the pose. It is easy to mistake this for a relative dwell and
reject the unit: the old *declared* clocks for `Play Dead` (1.68 s) and `Open
Fire` (1.4 s) made 0.1 s per unit look impossible, but the measured clocks are
16.781 s and 13.907 s. A *run* of records with it set is the block a routine
repeats: `Swing Arms` 1–4 is A,B,A,B, `Twist Butt` 1–6 is A,B,A,B,A,B, `Push
Ups` 4–6 is A,B,A. How many times is nowhere in the file — `Do Squats` stores
one squat for an 18 s action, and the robot keeps squatting. `--repeat N` plays
the marked block N times and records the decision in the output, so the count is
visible instead of silently assumed.

A byte is always a position, and **0 always means neutral**. That looks wrong
in Push Ups, whose pump block stores 0 on both thighs while the arms are
pumping, as if the robot stood up in the middle of its own push-up. It is not
wrong: a robot lying face down has its legs straight, and straight is the
thighs' neutral. The block reads as a push-up only once the *body* is on the
floor, which is a display problem, not a missing byte.

Four things the bytes do not carry, and where they are decided instead:

* **Direction** — the file only says how far, never which way. `K1.ACTION_DIR`
  in `src/k1.js` is one table of signs, chosen to agree with the presets that
  were checked on the model (shoulders pitch forwards like a punch, the arms
  abduct outwards like ARMS OUT, the elbows fold like ELBOWS, the legs fold the
  way CROUCH does). It is a labelled guess; a wrong one is a one-line fix there.
* **Which parts touch the floor** — a K1 has no waist servo, so the pelvis and
  torso are one rigid piece and the display has to decide how the body meets
  the world. For an ordinary pose it plants the support sole flat and in place,
  then tips and slides the pelvis with the leg chain: a crouch cancels its own
  angles and stays upright, a bow tips over. The same solve has a sideways
  half: when the two hip servos differ (Celebrate, the wave and tilt moves) the
  stance under the body changes. A rigid pelvis cannot keep both soles exactly
  where they stood, and rolling it would lift the hip on the long leg - and
  with the knees still straight that lifts the foot - so the display keeps the
  pelvis upright and slides it sideways until the midpoint between the feet is
  back under the body. Both soles stay on the floor, the splayed foot rocking
  onto an edge, and the pelvis swings left and right: that is the hip sway, not
  a foot shuffle. When both hips turn outward equally (Open Legs) the stance
  simply widens and the pelvis stays centred. For an action that says
  `contact: "hands"`, it instead solves the body pitch for **palms and toes at
  once**, at that action's keyframes, and interpolates the result between them.
  The pitch has to be solved per keyframe rather than live: the same servo
  counts read as "standing" at the pump keyframes, and a live solve jumps ~69°
  when the solution branch moves. A small local correction on each frame
  (`contactSeat`) keeps the palms level with the soles between keyframes: it
  runs in both directions when both keyframes around the frame are contact
  frames, and only when the palm would actually sink when they are not — so the
  push-off is not dragged back down. For an action that says `contact: "seat"`,
  the standing solve is wrong: a floor sit is carried by the pelvis and legs,
  not by flat soles. The torso therefore stays upright, the pelvis stays over
  its own origin, and the whole model is lowered until its lowest part — sole,
  calf, pelvis or palm — rests on the floor. That is what stops Take A Seat
  from pitching past horizontal and folding its legs behind the body. No servo
  count changes — it is geometry, the same kind of correction as the floor
  drop.
* **How a flip leaves the floor** — Back Flip, Front Flip and Keep Rolling
  Backward carry no root translation, no root rotation and no push-off impulse,
  so the pose alone cannot say which way the body turns. `flip` in
  `src/actions.js` is the display hint generated from the action name: `+1`
  rolls backwards onto the back, `-1` forwards onto the front. The root is then
  solved, not authored. Every part's mass, centre and inertia come from the
  model's own geometry; gravity plus sequential-impulse floor contacts with
  Coulomb friction integrate one planar track for the whole take, and the
  angular momentum the servo-driven tuck carries (`hrel`) is what starts the
  spin. The solve runs at 1/120 s: at 1/60 s the contact choice is chaotic, and
  a 1e-7 change flipped the outcome. The arms are excluded from the rigid-body
  solve — a swinging hand otherwise pole-vaults the body into a second roll —
  but their geometry still takes part in *seating*: from the triangle on, Back
  Flip's lowest palm joins the floor line, so the body is seated on the hands
  instead of the arms being rotated to meet the floor (see below). The owner's
  mark is the exception: that still levels the head and both palms together
  (see below). Back Flip also adds a small move-only `armClear` swing so the file's
  own hands do not
  pass through the floor as the body rolls. Every flip's
  last keyframe is neutral, so the free solve would snap the body upright at
  the file's end; instead the root is blended from the solve back to the
  neutral standing root over the final segment (starting at the last non-neutral
  keyframe), with the pelvis height solved so the lowest body hull stays on the
  floor. `FLIP_SETTLE = 1.5 s` then holds that standing pose. The readout and
  deep links still count only the measured file clock, and moving a slider only
  freezes the take and moves that one joint. A pose dialled during a flip is
  re-seated vertically on the lowest flip-body part, and if the dialled joints
  are legs its lean is re-solved from the leg chain: the free track was solved
  for the file's own limbs, so a shortened leg would otherwise leave the robot
  hanging in the air, and a crouch dialled to match a robot still winding up
  would be frozen standing instead of leaning back. The shared direction/gain
  table is also wrong for one stunt: read with it, Back Flip's wind-up folds
  the thighs the opposite way from the robot and lifts the soles 230 mm off the
  floor. The owner dialled the legs to the real machine at 1.852 s
  (73/173/168 and 177/78/84); fitting that pose to this file's own magnitudes
  at the same second gives a **move-only** remap — thigh ×−0.61, calf ×1.39,
  ankle ×0.79 — stored as `legGain` on `backflip`. The owner's later mark has
  the legs stretched at about 2.8 s, for the momentum of the flip, so `legGain`
  now carries time-`keys` with a second fitted set: thigh ×−0.18, calf ×0.05,
  ankle ×0.75, reached by 2.6 s and held through 2.95 s, then blended back to
  the file's own shape by 3.25 s for the roll. The first set is on through the
  wind-up up to 1.852 s and held through the owner's 2.022 s beat, so the
  display reaches the measured counts (72/175/168 and 176/80/84, within two
  counts) with the feet planted and keeps that bend as the head lands: at
  2.022 s it reads 130° of lean, head and palms on the floor, rather than the
  decoded pose's 224/22 thighs that snap the legs the other way. At 2.856 s
  the stretched set gives 112/63/114 and 134/187/139 against the measured
  112/66/113 and 135/189/138 (within three counts), with no slider touched.
  There is no near-neutral leg frame in the `.sh` at that second — its only
  all-zero leg records are the neutral start and end — so this beat is fitted
  from the dialled pose rather than read from the bytes. Neither gain is a new
  servo value: the bytes and the free flip solve keep using the file's own
  shape. Only Back Flip changes. The same remap also
  drives the root: throughout that wind-up the lean is solved from the leg
  chain, not left at the free track's upright pitch, so plain playback bends
  back over both planted soles (38° at 1.369 s) without needing Recall's
  one-count slider nudge. One beat is taken from the owner rather than the
  solve: `tripod: 2.0` on Back Flip is the second at which
  "head, and both arms form a triangle on the floor". The free solve has no arm
  contacts, so at that second it ran both palms under the floor with the head
  still 27 mm up. The display now solves the root that puts the head and both
  palms level and re-seats them on the floor at that beat, letting the palms
  carry the body from the moment they reach it (about 1.82 s). The owner also
  marked the robot still upside down on its head and arms at about 2.8 s (the
  page clock is approximate: the arms in that mark match the file at 2.255 s).
  The free solve had already lifted the head 180 mm by 2.856 s, so
  `tripodUntil: 2.95` holds the three-point contact through that beat and then
  blends the root back to the free roll over 0.35 s. At 2.856 s the display
  reads 143° of lean with the head at 0 mm, palms at 0/2 mm and the soles
  750 mm up, now with the legs stretched. Dialling stretched legs there used
  to stand the robot up (head 736 mm, soles on the floor): the leg-chain lean
  solve is confined to the wind-up, before the triangle, so the head and arms
  own the root from then on.
  The owner also caught a second bug at 3.039 s: with the body rolling onto its
  back, the file's fixed arm angle dragged the hands under the floor (read
  −97/−152 mm in the mark). The free solve has no arm contacts, so `armClear`
  swings both arms toward the body by a small keyed amount from 2.95 s, peaking
  near 3.3 s and back to the file's own shape by 4.0 s. Fitted from the
  abduction that keeps the lowest hand level, it stays well inside the arm's
  travel and keeps every hand within 2 mm of the floor across the whole roll
  without moving the head or the body's root. It is a display hint on this move
  only: the decoded arm bytes and the flip solve are unchanged.
  A third mark found the same bug with a pose *dialled by hand*: fully abducted
  arms and folded elbows (leftArm 33, leftHand 37, rightArm 224, rightHand 204)
  dropped both palms ~90 mm under the floor at 141° of lean. The keyed swing
  above was fitted to the file's arms and cannot know a dialled pose, and
  rotating the arm servos to reach the floor would change the angles the owner
  dialled. Those joints are the observation, so from the triangle on the
  *body* is seated on the lowest palm instead: the root height is the more
  restrictive of the body hull and the two hands, with `armClear` gating the
  extended window on Back Flip only. Every dialled joint — all 17, arms and
  legs — is shown exactly as set; no hand passes under the floor for any pose
  across the whole roll. At 141° the hands sit ~92 mm below the head, so with
  the hands planted the head necessarily lifts (about 91 mm at the 2.96 s
  beat). The owner's own mark is the exception: it needs the head and both
  palms on the floor together. Since the dialled arms cannot change, its root
  pitch is solved to the level crossing for those exact joints — about 178° —
  and all three contacts are dropped onto the floor. Reached by scrubbing or
  deep-linking anywhere in the 3.35–3.55 s band, or by opening the mark, the
  still then reads head 0 mm, hands 0/1 mm, with the body flipped over them and
  every joint exactly as dialled. The 141° and low heights in the record came
  from an older head-only solve; keeping the real triangle changes them. This
  changes no decoded byte and no joint: it is a display seating decision,
  Back Flip only.
  That triangle is a root solve from the file's own pose, not an arm IK: the arm
  servos come straight from the script (as modified by the file's own clearance
  swing), and a dialled pose overrides them verbatim.
  Back Flip therefore leans back, plants
  its palms, puts its head down with them at 2.0 s, holds that triangle through
  the upside-down beat, rolls over it and lies on its back from about 3.3 s to
  5.7 s (about 74° of pitch, head clear, feet raised by the bent knees), then
  stands up as the last keyframe arrives at 6.47 s. With the remap the wind-up
  instead reads 95° of lean at 1.852 s with both soles on the floor, keeps the
  bend through the triangle at 2.0 s (the knees stay folded under the body,
  soles still low), stretches the legs for the upside-down beat and blends back
  to the file's pose by 3.25 s for the roll.
  Front Flip is
  the same track mirrored in X: it tips to within about 4 cm of its head at
  2.52 s — the measured clock leaves it short of the floor — and stands back up
  by 8.67 s. Keep Rolling Backward puts its head down at about 3.3 s, lies on
  its back for most of its 17.9 s, and also stands up at the end.
* **How often a routine repeats** — the file marks the block that repeats but
  never how many times, and the robot's own count is not readable over BLE
  either. `--repeat 3` on Push Ups is the observed behaviour (three pumps, the
  way the real robot does it), recorded as `repeat: {core: [4,5,6], times: 3}`
  in `src/actions.js` so the assumption sits next to the data it modifies. If
  the robot turns out to pump twice or four times, that one number changes.

Neither the harness nor the model can settle the direction: the honest test is
to play it on the robot and watch which way the hips go. To inspect one frame
in the browser, `k1.html#run/<key>/<seconds>` starts that action and holds the
pose at that second; `#run/<key>` plays it from the start, and `#run` alone just
starts the demo (music stays off). Keys are the move names reduced to letters
and digits, which is also what the browser exposes on each `<option>`:

| link | what it holds |
| --- | --- |
| `#run/pushups/0.854` | the deepest hinge |
| `#run/pushups/3.035` | the first deep pump |
| `#run/playdead/8.0` | halfway through the 16.781 s Play Dead |
| `#run/backflip/2.05` | Back Flip's head touch, mid-roll |
| `#run/lookleft` | starts the card-only 1.375 s look |

### Comparing with the real robot

The link is one-way: the K1 never reports a joint angle back, so neither a
harness nor the page can check the model against the machine by itself. The
`RECORD` strip closes that loop by hand. Play the move in the browser next to
the real robot, press `V` when the robot does something nameable, and type the
name first if you like (`Enter` marks and clears the box). Each mark records

* the action and the second of its own clock — the page's clock, so approximate,
* your note,
* every joint value the file asks for at that second,
* the model's solved lean and every part's height above the floor, in mm,
* and, if you paused and dialled the sliders to the robot's pose first, that
  pose as well, plus how many joints differ from the file.

A dialled pose outlives the take: playing the action overwrites the sliders, but
`U` (or the `recall` button) restores the last pose you set by hand, snaps it on
screen and puts the clock back to the second it was dialled against, so the mark
can still be made after the take has moved past. Only joint edits are kept — a
preset or `R` is the demo's own pose, not an observation — and the memory is not
saved across a reload.

`copy` produces one JSON object (`format: "k1-record/1"`) ready to paste into a
report; nothing leaves the machine on its own. Clicking a mark puts the take
back on that second with the file's pose and root, and the bar under the move
list can be dragged to any other second, so a note like "head touches at 2.05 s"
can be checked against the model immediately. A mark without a `pose`
key is the model's own pose — it is not evidence that the robot agreed. The
BLE interface has no return channel, so this is the only way the real robot's
behaviour gets into the repo, and it is a human observation, not a measurement.

### Back Flip: what the owner saw, and what the file says

The display was built to the owner's account: *"for the back flip the robot
leans back and it touches the floor with the head in order roll over to the
back"*, and at **1.565 s** *"the robot is completely bending to the
back so that it falls over by its own weight on its head stabalized left and
right by the arms"*. Later in the same movement: *"head, and both arms form a
triangle on the floor"*.

A mark taken during that take records the corrected page second `t = 1.565 s`,
`lean = 101.7°` and `diff = 7`. The two thighs, two calves, the left ankle and
the two shoulders were dialled away from the file. The corrected thighs point
the opposite way from the file's tuck (left 41 against 205, right 200 against
39, in raw counts), and the calves fold the other way too (left 163 against
119, right 82 against 132); only the left ankle stays close (+9). The
shoulders are pulled apart from the file's 119/129 (96/169). Read that as a
stiff topple: the robot fell on its own weight with its legs extended and its
arms out, not as the tight tuck the bytes describe. The note on the mark is the
owner's; the model's own measurement at that page second is not evidence of the
robot's pose.

What the display does at that second depends on whose pose it is showing.
With the file's own limbs the root is still nearly upright at 1.565 s (4.9°);
the free solve only reaches 101.7° at about 1.97 s, and by then the feet are
about 0.5 m up. The mark's `model` field is the file's pose 80 % of the way
from the 1.078 s lead-in to the 1.687 s bend, so that is what the file alone
says there. Replaying the mark's dialled pose at 1.565 s instead gives 109.8°
backward, the lowest sole on the floor (the other 14 mm up) and the head
135 mm clear - against the mark's 101.7° and 177 mm - because a dialled leg
chain is what tips the body, not the free track solved for the file's limbs.

A second mark records `t = 1.739 s`, the note "not bending backwards", and
`lean = -1.5°` with nine joints dialled. Its legs are a crouch, and the old
display kept the free track's nearly-upright root for it, so the body sat on
the folded leg instead of leaning back. Re-solved from its own leg chain it now
leans 67.5° backward with the feet planted and the head 427 mm up (the mark
measured 430 mm). That is the rule now: a head or arm edit freezes the take and
keeps its root; a leg edit re-solves the lean from the legs.

Two limits are worth stating plainly:

* the page clock and the file clock are not the same clock. The measured trace
  puts the first keyframe at 1.078 s and the head-down triangle at 2.0 s;
  the owner's corrected 1.565 s is earlier than that. An app clock that
  started counting at the first keyframe would put the head-down beat at
  0.92 s, so excluding the lead-in does not explain it either. Treat the
  seconds as approximate until the same take is filmed and counted. The
  3.461 s mark is exactly this case: its `model` block is the file's 2.958 s
  pose, so the file at 3.461 s is a later 79° frame. The observation is filed
  as a generated `markPose` hint on Back Flip: scrubbing or deep-linking
  anywhere in the 3.35–3.55 s band shows the dialled joints and a level root,
  with the head and both palms on the floor together. Times outside that band
  still show the file, and playing the take drops the override.
* the leg remap is fitted from two hand-dialled marks, not from a sweep of the
  robot's travel. It makes Back Flip's wind-up and stretched-leg beat reach the
  poses the owner measured, but other magnitudes in the same stunt are inferred
  from those two fits, and no other move is touched. Confirming the two beats
  and the stretch in between needs the same take filmed and counted.
* the triangle is simulated at the level of the root, not of the arm servos.
  The arms stay out of the free rigid-body solve (a swinging hand pole-vaults
  a rigid body), so they cannot push the pelvis around the way the robot's
  position-controlled arms do. Around the observed 2.0 s the root is instead
  solved from the head and palm geometry: the palms catch the body first and
  the head comes down to them. That reproduces the shape and the sequence, not
  the load path — no arm IK, and no force feedback. After the triangle the roll
  is the free solve again for the body's lean and travel, but Back Flip keeps
  the palms in the *seating*, so they stay on the floor through the roll no
  matter which pose is on screen.

The file-side evidence for the motion is the progress trace, not the owner's
clock: `backflip` has ten records, the head-down keyframe arrives at 2.0 s, and
the measured clock ends at 6.469 s, after which the display rises to standing.

## Verifying without a browser
Ten harnesses run the real source under Node. None of them need a GPU.

| Harness | What it proves |
| --- | --- |
| `k1test.js` | 17 unique indices, travel contains neutral, soles on the floor, one joint moves only its subtree, clamping, mirror-mount counts, every preset does what its label says, the panel matches the table, the move browser lists all 90 moves with 86 playable and four disabled, and the decoded Push Ups: 18 keyframes (the file's 12 with the marked pump block played 3×), samples all inside travel, starts and ends neutral, plays for the measured 9.625 s, cancels on manual input, bends the right joints, keeps the palms level with the soles between keyframes, never goes through the floor, dips the pelvis three times, once per pump, keeps Take A Seat upright with its pelvis on the floor and its feet forward, checks that `Open Legs`/`Celebrate` widen the stance rather than crossing the feet, and that Celebrate swings the pelvis over both planted soles (no foot lift, at the keyframes or between them) while Open Legs keeps the pelvis centred, and the flip solve: Back Flip leans back, puts its head on the floor, lies on its back and stands up, its move-only leg remap reaches the owner's dialled counts at 1.852 s with the soles planted while the decoded magnitudes and the free track stay untouched, plain playback leans back over both planted soles at 1.369 s without a dialled pose, the triangle holds the owner's later upside-down beat at 2.856 s, plain playback stretches the legs there to the owner's dialled counts without a slider touched and keeps the file's own bytes underneath, and stretched legs dialled there do not stand the robot up, its hands do not sink through the floor during the roll and the arm clearance is display-only with the decoded bytes untouched, a dialled pose keeps all 17 dialled joints exact and both hands on the floor at the 141° beat (the head lifts), never drags a hand under it or changes a joint through the roll, and the owner's 3.461 s mark keeps its dialled joints with a level head-and-palms triangle both when scrubbing to that second (from a generated hint, without pasting) and when opening the pasted record, with neighbouring seconds still on the file and playing dropping the override, Front Flip mirrors it forwards and tips short of its head, Keep Rolling Backward rolls its head onto the floor and also stands up, no body part sinks through the floor, and the track is cached, seek-deterministic, held through the settle and shown from the profile camera, and the manual recorder: a mark keeps the action clock through a slider change (which freezes the take and keeps its clock), stores the file's pose and the model's head height, adds a pose of its own only when the sliders moved, survives a JSON round trip, seeks back to its second, the progress bar holds any second and keeps a playing take playing, the pose dialled by hand survives the take that overwrites it and comes back on its own second, a leg dialled during a frozen flip is re-seated on the floor and leans the way its own leg chain says instead of keeping the file's root, and a keystroke typed into a text field no longer fires the shortcuts; and Back Flip's observed triangle: the palms carry the body before the head lands, at 2.0 s the head and both palms are on the floor with the corrected knees still folded low, the leg bend survives the owner's 2.022 s mark, the triangle holds through the upside-down beat at 2.856 s even with stretched legs dialled, and the beat is bounded before the free roll takes over |
| `smoke.js` | both builds load, run 240 frames, and issue real draw calls |
| `audiofail.js` | a broken `AudioContext` can never stop the render loop |
| `glslcheck.js` | the VS/FS pairs agree on varyings; nothing undeclared; no scalar/vector assignment mismatch (the sky once declared `float` from `floor(vec2(...))`) |
| `guardtest.js` | the error panel never covers a working demo |
| `fallbacktest.js` | a driver that rejects the shaders degrades instead of dying, and a working build passes the load guard's dependency check |
| `degraderest.js` | a rejected program costs only its own effect, not the others |
| `orienttest.js` | the image is the right way up |
| `shadesim.js` | every part of the robot resolves to a lit, non-black surface |
| `controltest.js` | every control still does something in the fallback renderer |

```sh
node test/bundle.js          # rebuild k1.html
node test/check.js           # all ten harnesses
node test/check.js --rebuild # rebuild, then check
```

### What the rig harness cannot prove

`k1test.js` checks that the model is *internally* consistent: opposite deltas on
a mirrored pair are a mirror image, a preset written that way really is
symmetric, a nudge carries children and nothing else, the soles sit at y ≈ 0, a
decoded action never leaves a servo's travel and starts and ends neutral. It
cannot check that
`leftShoulder` really pitches rather than abducts, or that a knee flexes the way
the sign says, or that a `.sh` magnitude travels the direction `ACTION_DIR`
claims. That needs 17 single-joint nudges on the real robot, one per
joint, which is exactly what the panel is for:
move one slider, watch the robot, and if the joint went the wrong way, change
its `axis` or `sign` in `src/k1.js`.

### Zero normals, and why a body goes black

`normalize(vec3(0))` is **undefined** in GLSL ES 1.00. A driver that returns
NaN for it turns every such fragment black, and a black body reads on screen as
a hole in the robot. Three layers guard against it:

| Layer | What it does |
| --- | --- |
| `geo.js` `pushFlat` | emits a **per-face** normal, and rescues a degenerate triangle with the outward direction from the part centroid |
| `geo.js` `xform` | turns a degenerate transformed normal into `(0,1,0)` instead of passing zero through with `\|\| 1` |
| `FS_SCENE` / `FS_MIN` | guard the division: `nl > 0.0 ? vN / nl : vec3(0,1,0)` |

Every generator builds an unshared triangle soup with sequential indices, so
true flat shading costs nothing — one normal per triangle, given to its three
vertices.

`shadesim.js` is the harness that would have caught the old bug. The mock GL
models buffers, attribute pointers and matrix uploads, and the harness resolves
each vertex the way a driver would and runs the real `FS_SCENE` maths on it.

### Degradation is per program, not per demo

The renderer builds seven programs — `post`, `ring`, `sky`, `shadow`, `floor`,
`scene`, `blit` — independently. A rejection is recorded and that one effect is lost;
every program the driver did accept keeps running.

| Rejected | You lose | You keep |
| --- | --- | --- |
| `post` | scanlines, dither, aberration, quantisation | everything; the blit copies the buffer instead |
| `sky` | the sky shader | everything else, against a flat horizon |
| `ring` | impact rings | everything else |
| `shadow` | the contact shadow | everything else |
| `floor` | the floor grid | everything else |
| `scene` | rim and specular on the robot | sky, floor, shadow, post — the robot goes flat |

Only two things are fatal, and both are things nothing can be drawn without:

- **`blit` is rejected** — it is the only route from the offscreen buffer to the
  screen, so there is no image at all. The demo says so and stops.
- **`scene` and `min` are both rejected** — there is no way to draw the robot.

### The fallback robot renderer

When the lit `scene` program is rejected, the robot is drawn with two
deliberately plain shaders instead. **A keypress that changes nothing is
indistinguishable from a dead demo**, so the fallback honours the controls too:

| Key | Full renderer | Fallback |
| --- | --- | --- |
| `W` wireframe | lines shaded by the light | same, via the same wire index buffer |
| `C` colour cycle | `hue()` rotates colours in the fragment shader | `M4.hueRotate` rotates them on the CPU — same Rodrigues formula, and `controltest.js` checks the two agree to 1e-6 |
| `N` shading mode | per-face normal, quantised; then mono light | same, from a real transformed normal varying |
| `H` `M` `SPACE` `R` `1`–`4` drag wheel | unchanged | unchanged |

The floor stays a flat plate only when the `floor` program is *also* rejected.
The HUD shows `*FLAT*` in the corner when the robot shading has degraded.

### Two textures, two orientations

The renderer hands the post pass two textures that are **not** oriented alike,
and conflating them flips the whole scene:

| Texture | Row/v origin | Flip when sampling? |
| --- | --- | --- |
| scene FBO | already in GL orientation — `v=0` is the bottom, same as the screen | **no** |
| HUD 2D canvas | `texImage2D` puts canvas row 0 (the top) at `v=0` | **yes** |

A hardcoded flip in the blit is what made the robot appear upside down, so the
choice is a `uFlip` uniform and `orienttest.js` checks both call sites.

## If it does not come up

The demo never dies silently. Whatever goes wrong is reported three ways at
once, none of which hide the canvas:

* a small red panel, bottom-right, `pointer-events: none` so it cannot steal a
  click — it clears itself after 14 s or on any click or keypress;
* the intro panel's "click to start" line, which is where you are looking;
* the browser tab title.

| Symptom | What happened | What to do |
| --- | --- | --- |
| Panel says `NO WEBGL IN THIS BROWSER` | hardware acceleration is off, which is the usual cause | Chrome/Edge: Settings → System → enable "Use graphics acceleration". Firefox: `about:config` → `gfx.webgl.force-enabled = true`. Restart the browser. |
| Panel says `DEGRADED RENDERER` | the driver refused some shader programs; the rest are running | Nothing. Whatever it did accept is still running. The quoted log is the only thing needed to fix the shader. |
| Panel says `BLIT SHADER FAILED` | the driver refused the one shader that copies the offscreen buffer to the screen | Post the log, or try another browser — it is driver-specific. |
| Panel says `BOTH SHADER SETS FAILED` | the driver refused both the lit robot shader and the flat stand-in | Post the log, or try another browser. |

If keys do nothing at all, `main.js` returned during init, so one of the three
above definitely happened. Read the panel.
