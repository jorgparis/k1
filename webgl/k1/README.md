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

## Layout

`k1.html` is generated — edit the files below and re-run the bundler rather
than editing the bundle.

| File | Contents |
| --- | --- |
| `k1.html` | **generated** single-file build; this is the one to open |
| `index.html` | page shell, load-failure guard, script order, servo panel markup |
| `style.css` | retro shell, intro panel, servo panel, error panel |
| `src/math.js` | `M4` (compose is `T·Rx·Ry·Rz·S`), `V3`, scalar helpers |
| `src/geo.js` | flat-shaded geometry generators, merge, wireframe edge list |
| `src/gl.js` | program / mesh / FBO / canvas-texture helpers |
| `src/shaders.js` | sky, grid floor, planar shadow, scene, post |
| `src/k1.js` | the `JOINTS` table, 19-part rig, neutral pose, clamping, pose, `.sh` direction + sampler |
| `src/actions.js` | **generated** by `test/genactions.py` from the phone menu and the card's `.sh` files; `window.K1ACTIONS` + `K1ACTION_LIST` |
| `src/hud.js` | 320×200 1-bit HUD |
| `src/music.js` | chiptune sequencer |
| `src/main.js` | GL setup, render passes, camera, servo panel, presets, action player, input |

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
`0b xx` (the action id), the last `fb fb`. Timing is not stored; the tool derives
it from `max joint change / speed` per keyframe and stretches marked keyframes by
their emphasis (below), then scales the whole action to the clock the phone data
carries. That clock is the one measured on the robot where it exists (Push Ups
9.625 s, Play Dead 16.781 s, Open Fire 13.907 s), `robot.json`'s declaration for
the four moves with no script, and a record-count estimate (~700 ms per record,
the median of the measured card-only scripts) for the 14 card scripts nobody has
timed. The browser says `measured`, `declared` or `estimated` next to every
action, so an estimate is never presented as a measurement.

The flags byte is an **emphasis on that keyframe, relative to the keyframe's own
time**, not an absolute dwell (`Bow` 30, `Propose` 50 then 85, `Do Squats` 20 at
the bottom, `Lean Forward/Back` 30). It cannot be seconds: the same 45 sits on
the same cannon pose in `Open Fire` (1.4 s) and `Fire In The Hole` (7.4 s), the
same kneel pose is 35 in `Kneel, Please` but 50 in `Propose`, and no value
anywhere on the card exceeds 85. Reading it as 0.1 s per unit would put
`Play Dead` at 6.6 s and `Open Fire` at 7.3 s against their declared 1.68 s and
1.4 s. `shdecode.py` lets it lengthen the move away from the marked pose and
then rescales to the declared clock, so `Play Dead` really does lie still and
stretch inside its 1.68 s. A *run* of records with it set is the block a routine
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

Three things the bytes do not carry, and where they are decided instead:

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
| `#run/lookleft` | starts the card-only 1.375 s look |

## Verifying without a browser

Ten harnesses run the real source under Node. None of them need a GPU.

| Harness | What it proves |
| --- | --- |
| `k1test.js` | 17 unique indices, travel contains neutral, soles on the floor, one joint moves only its subtree, clamping, mirror-mount counts, every preset does what its label says, the panel matches the table, the move browser lists all 90 moves with 86 playable and four disabled, and the decoded Push Ups: 18 keyframes (the file's 12 with the marked pump block played 3×), samples all inside travel, starts and ends neutral, plays for the measured 9.625 s, cancels on manual input, bends the right joints, keeps the palms level with the soles between keyframes, never goes through the floor, dips the pelvis three times, once per pump, keeps Take A Seat upright with its pelvis on the floor and its feet forward, checks that `Open Legs`/`Celebrate` widen the stance rather than crossing the feet, and that Celebrate swings the pelvis over both planted soles (no foot lift, at the keyframes or between them) while Open Legs keeps the pelvis centred |
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
