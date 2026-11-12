/* k1.js - the Robosen Interstellar Scout K1: 17 mapped joints, one rig.

   Every part here is a *joint*, named after the servo that drives it, so the
   model and the protocol agree by construction:

     leftThigh  leftCalf  leftAnkle  leftHip  leftFoot      (5 per leg)
     rightThigh rightCalf rightAnkle rightHip rightFoot
     leftShoulder leftArm leftHand                        (3 per arm)
     rightShoulder rightArm rightHand
     head

   The numbers are the real ones: index, neutral and travel come from the K1's
   own `robot.json`, so a slider stops exactly where the servo does and sends
   exactly the byte the library would send.

   What is *not* known, and is therefore a labelled guess:

     - which axis each servo rotates about, and in which direction.

   `robot.json` names the joints and gives their ranges; it does not say whether
   `leftShoulder` pitches or rolls. The table below picks the arrangement that
   makes a humanoid (thigh pitches, calf bends the knee, head yaws) and mirrors
   the right side about 248, which is the symmetry the neutrals actually show
   (leftShoulder 212 / rightShoulder 36, leftCalf 60 / rightCalf 190). If a real
   nudge proves one wrong, change that joint's `axis` or `sign` here and the
   model follows - nothing else needs to know.

   Geometry is low-poly boxes and tapered limbs, flat shaded, in the walker
   house style. The robot faces +X, its left is -Z, its right is +Z.
*/
(function (global) {
  'use strict';

  const G = global.Geo, DEG = Math.PI / 180;

  /* ------------------------------------------------------------------ dims */
  const D = {
    hipH: 0.49,          // pelvis origin above the floor (soles land at 0)
    hipZ: 0.085,         // half hip spacing (abduction mounts)
    thigh: 0.20, calf: 0.19,
    ankleH: 0.05, footH: 0.04,
    footLen: 0.135, footW: 0.075,
    waist: 0.10,         // pelvis -> torso
    chest: 0.24, neck: 0.20,
    shoulderY: 0.175, shoulderZ: 0.135,
    upper: 0.13, fore: 0.12, hand: 0.07,
    headH: 0.13,
    // One knob for the whole rig: how far one count turns a servo.
    //
    // 0.90 deg/count is not a guess about the servo's gearing, it is what the
    // card's own actions say. The scripts treat 100 counts as a right angle:
    // `Left Punch` swings the shoulder 100 and the hand reads horizontal,
    // `Open Arms` uses 99/100 for arms straight out, `Do Squats` 80 thigh /
    // 90 calf / 30 ankle for a squat. The shoulders' full 190-count travel is
    // then 171 degrees - over the head and down the back - and the calf's
    // largest value in the corpus, 160, is 144 degrees of knee flex, which is
    // also what a calf that can touch a thigh should do. 0.40 made every one
    // of those read as a shallow bow.
    //
    // Change this and every joint scales together - it is the only global
    // fudge here.
    degPerCount: 0.90
  };

  /* ---------------------------------------------------------------- colors */
  const C = {
    shell: [0.82, 0.85, 0.88],
    shellHi: [0.94, 0.96, 0.98],
    shellLo: [0.55, 0.59, 0.66],
    dark: [0.17, 0.19, 0.24],
    rubber: [0.10, 0.11, 0.14],
    orange: [0.98, 0.52, 0.12],
    red: [0.86, 0.20, 0.17],
    steel: [0.42, 0.45, 0.52],
    visor: [0.25, 0.95, 1.00, 0.95],
    glow: [0.55, 0.95, 1.00, 0.55]
  };

  /* --------------------------------------------------------------- helpers */
  function P(name, parent, pos, geo) {
    return {
      name, parent, pos: pos.slice(), rot: [0, 0, 0], geo,
      world: global.M4.create(), flat: global.M4.create(), nm: new Float32Array(9)
    };
  }

  // a limb segment hanging *down* from its joint: origin at the top.
  // Geo.limb already spans y = 0 .. -h, so the joint stays where the table puts it.
  function bone(wTop, dTop, wBot, dBot, h, col) {
    return G.limb(wTop, dTop, wBot, dBot, h, col);
  }

  function jointBall(r) {
    return G.xform(G.sphere(r, 6, 4, C.dark), {});
  }

  /* ----------------------------------------------------------------- table */
  /* index/neutral/min/max are copied from robot.json. `axis` is the axis the
     servo turns about *in world terms*; `sign` is the direction a positive
     count turns it. The axes are the guess; the numbers are not.

     Two facts from robot.json drive the sign convention:

       1. Every mirrored pair's neutrals sum to about 249 (shoulders 212+36,
          calves 60+190, thighs 129+118). That is the signature of two copies
          of the same servo mounted mirror-image, so a SYMMETRIC pose needs
          OPPOSITE raw deltas on the pair: left +d, right -d.

       2. A pair's neutral is usually at one end of its travel (the right arm
          is exactly at its minimum; the shoulders are 10 and 0 counts from
          their ends). The long remaining travel is the useful direction, so
          `sign` is chosen to make it the anatomically useful one: knees and
          elbows flex, shoulders pitch forward, arms abduct outwards.

     For a pose that is a mirror image about the body's centre plane:
       axis z (pitch) - both sides turn the *same* way  -> signs opposite
       axis x (roll)  - the sides turn opposite ways     -> signs equal
       axis y (yaw)   - the sides turn opposite ways     -> signs equal

     Which axis each servo is on is the remaining guess. The card's actions
     pin it: `Left Punch` travels the shoulder 100 counts and the hand reads
     horizontal (pitch about z), then travel on `hand` extends the arm - the
     elbow, also about z. `say hello` swings the shoulder 100, bends `hand` 70
     and wiggles `arm` only 15. `Ready To Strafe` holds both shoulders at 100
     with the elbows near 10, aiming, while `Open Arms` needs `arm` to take
     the arms out to the sides (roll about x). So:

       shoulder - pitch, the whole arm swings fore/aft
       arm      - abduction, the whole arm swings out to the side
       hand     - elbow, the forearm folds
       thigh/calf/ankle - pitch (the calf's sign is negative: a knee folds
                          the other way from a hip)
       hip      - roll (tiny range, hip abduction)
       foot     - yaw (tiny range)
       head     - yaw

     A symmetric "both arms forward" is available on the shoulders, whose
     neutrals are at one end of travel: both swing a long way forwards and only
     a few counts back. */
  const JOINTS = [
    { name: 'leftThigh',  part: 'thighL',    index: 0,  neutral: 129, min: 29,  max: 229, axis: 'z', sign: 1 },
    { name: 'leftCalf',   part: 'calfL',     index: 1,  neutral: 60,  min: 10,  max: 220, axis: 'z', sign: -1 },
    { name: 'leftAnkle',  part: 'ankleL',    index: 2,  neutral: 106, min: 26,  max: 226, axis: 'z', sign: 1 },
    { name: 'rightThigh', part: 'thighR',    index: 3,  neutral: 118, min: 18,  max: 218, axis: 'z', sign: -1 },
    { name: 'rightCalf',  part: 'calfR',     index: 4,  neutral: 190, min: 30,  max: 240, axis: 'z', sign: 1 },
    { name: 'rightAnkle', part: 'ankleR',    index: 5,  neutral: 146, min: 26,  max: 226, axis: 'z', sign: -1 },
    { name: 'leftShoulder', part: 'shoulderL', index: 6, neutral: 212, min: 22,  max: 242, axis: 'z', sign: -1 },
    { name: 'rightShoulder', part: 'shoulderR', index: 7, neutral: 36, min: 6,   max: 226, axis: 'z', sign: 1 },
    { name: 'leftHip',    part: 'hipL',      index: 8,  neutral: 123, min: 103, max: 133, axis: 'x', sign: 1 },
    { name: 'leftFoot',   part: 'footL',     index: 9,  neutral: 123, min: 93,  max: 133, axis: 'y', sign: 1 },
    { name: 'rightHip',   part: 'hipR',      index: 10, neutral: 129, min: 119, max: 149, axis: 'x', sign: 1 },
    { name: 'rightFoot',  part: 'footR',     index: 11, neutral: 115, min: 105, max: 145, axis: 'y', sign: 1 },
    { name: 'leftArm',    part: 'armL',      index: 12, neutral: 223, min: 33,  max: 233, axis: 'x', sign: -1 },
    { name: 'leftHand',   part: 'handL',     index: 13, neutral: 116, min: 16,  max: 216, axis: 'z', sign: -1 },
    { name: 'rightArm',   part: 'armR',      index: 14, neutral: 34,  min: 34,  max: 224, axis: 'x', sign: -1 },
    { name: 'rightHand',  part: 'handR',     index: 15, neutral: 126, min: 26,  max: 226, axis: 'z', sign: 1 },
    { name: 'head',       part: 'head',      index: 16, neutral: 122, min: 42,  max: 202, axis: 'y', sign: 1 }
  ];

  /* Small demo poses, in raw counts relative to neutral. A symmetric pose is
     left +d, right -d (see the table comment); each is chosen to use the long
     side of that servo's travel and to stay off the stops. They show the
     mapping - none of them is a factory stunt. */
  const PRESETS = {
    neutral: { name: 'NEUTRAL', raw: {} },
    lookLeft: { name: 'LOOK LEFT', raw: { head: +70 } },
    armsOut: { name: 'ARMS OUT', raw: { leftArm: -150, rightArm: +150 } },
    elbows: { name: 'ELBOWS', raw: { leftHand: -90, rightHand: +90 } },
    crouch: {
      name: 'CROUCH',
      raw: {
        leftThigh: +75, rightThigh: -75,
        leftCalf: +150, rightCalf: -150,
        leftAnkle: +75, rightAnkle: -75
      }
    },
    hipRoll: { name: 'HIP ROLL', raw: { leftHip: +9, rightHip: -9, head: +20 } }
  };

  const byJoint = {};
  for (const j of JOINTS) byJoint[j.name] = j;

  /* ------------------------------------------------------- .sh direction */
  /* A stunt script on the card stores, for each servo, how far to travel from
     neutral - never which way. That direction is a guess, so it is kept here,
     in one table, next to the joint table it depends on.

     The guesses are not free: they are chosen to agree with the presets that
     were checked on the model.

       shoulder -1  the long travel pitches the arm FORWARDS, which is what
                    every reaching action uses (Left Punch 100, Push Ups 100
                    with the hands on the floor).
       arm      -1  the long travel takes the arm OUT to the side - the ARMS
                    OUT preset uses -150 there, and `lift your arms` 190 is
                    straight up.
       hand     -1  the long travel folds the elbow - the ELBOWS preset uses
                    -90 (the 100-count limit is a right angle), and `say
                    hello` bends it 70 to wave.
       thigh/calf/ankle +1
                    the CROUCH preset bends the legs with left +75/+150/+75,
                    so a positive magnitude flexes the same way. (The thigh
                    sign used to be -1; that would have bent the knee one way
                    and the hip the other, which is not a leg.)
       hip/foot  -1/+1
                    tiny range, and no card action drives them far; kept
                    consistent with the same sign rule so they cannot fight it.

     Left and right are mirror-mounted (their neutrals sum to ~249), so the
     right side takes the opposite sign: raw = neutral - m. The head is a
     single joint and takes +1.

     Every result is clamped to the servo's own travel, because the files do
     overshoot: Push Ups asks the thighs for 110 counts where the leg has 100. */
  const ACTION_DIR = {
    thigh: +1, calf: +1, ankle: +1,
    shoulder: -1,
    hip: -1, foot: +1, arm: -1, hand: -1, head: +1
  };

  function actionDir(name) {
    if (name === 'head') return ACTION_DIR.head;
    const side = name.slice(0, 4) === 'left' ? 1 : -1;
    const base = name.replace(/^(left|right)/, '').toLowerCase();
    return (ACTION_DIR[base] || 1) * side;
  }

  /* One decoded keyframe -> raw counts. */
  function actionRaw(frame) {
    const raw = {};
    for (const j of JOINTS) {
      const m = (frame.mag && frame.mag[j.name]) || 0;
      raw[j.name] = clampRaw(j.name, j.neutral + actionDir(j.name) * m);
    }
    return raw;
  }

  /* Where an action is at `t` seconds: linear between the keyframes either
     side, clamped at both ends. Interpolating magnitudes (not raw counts) means
     a frame where the file says "0" really is neutral, not "wherever the last
     pose left it".

     That matters in Push Ups, where the pump block stores 0 on both thighs. It
     is not a stand-up: a robot lying face down has its legs straight, and the
     thigh's neutral is straight. The file is right; it is the body that is on
     the floor, and `contact: "hands"` below is what tells the display that. */
  function actionPose(action, t) {
    const f = action.frames;
    if (!f || !f.length) return neutralPose();
    if (t <= f[0].t) return actionRaw(f[0]);
    const last = f[f.length - 1];
    if (t >= last.t) return actionRaw(last);
    let i = 1;
    while (i < f.length && f[i].t < t) i++;
    const a = f[i - 1], b = f[i];
    const span = Math.max(1e-6, b.t - a.t);
    const u = (t - a.t) / span;
    const raw = {};
    for (const j of JOINTS) {
      const ma = a.mag[j.name] || 0, mb = b.mag[j.name] || 0;
      raw[j.name] = clampRaw(j.name, j.neutral + actionDir(j.name) * (ma + (mb - ma) * u));
    }
    return raw;
  }

  function neutralPose() {
    const pose = {};
    for (const j of JOINTS) pose[j.name] = j.neutral;
    return pose;
  }

  function clampRaw(name, raw) {
    const j = byJoint[name];
    if (!j) throw new Error('unknown joint ' + name);
    return Math.max(j.min, Math.min(j.max, Math.round(raw)));
  }

  /* --------------------------------------------------------------- the body */
  function buildParts() {
    const parts = [];
    const add = (p) => { parts.push(p); return p; };

    /* ---- pelvis: the root, everything hangs off this ---- */
    add(P('pelvis', null, [0, D.hipH, 0], G.merge([
      G.xform(G.box(0.15, 0.10, 0.115, C.shell), { t: [0, -0.005, 0] }),
      G.xform(G.box(0.155, 0.035, 0.12, C.shellLo), { t: [0, 0.035, 0] }),
      G.xform(G.box(0.09, 0.05, 0.085, C.dark), { t: [0, -0.045, 0] }),
      // hip abduction mounts
      G.xform(G.cyl(0.028, 0.028, 0.04, 6, C.dark), { t: [0, -0.02, -D.hipZ], r: [0, 0, Math.PI / 2] }),
      G.xform(G.cyl(0.028, 0.028, 0.04, 6, C.dark), { t: [0, -0.02, D.hipZ], r: [0, 0, Math.PI / 2] })
    ])));

    /* ---- torso: fixed to the pelvis, no waist servo on a K1 ---- */
    add(P('torso', 'pelvis', [0, D.waist, 0], G.merge([
      G.xform(G.box(0.175, D.chest, 0.125, C.shell), { t: [0, D.chest * 0.5, 0] }),
      G.xform(G.box(0.185, 0.055, 0.13, C.shellHi), { t: [0, D.chest - 0.02, 0] }),
      // chest light, the one bit of orange you always see in the app
      G.xform(G.box(0.10, 0.075, 0.008, C.dark), { t: [0.082, 0.15, 0] }),
      G.xform(G.box(0.055, 0.04, 0.006, C.glow), { t: [0.088, 0.15, 0] }),
      // backpack
      G.xform(G.box(0.055, 0.14, 0.10, C.shellLo), { t: [-0.11, 0.13, 0] }),
      // shoulder pads, static: the servos are inside them
      G.xform(G.box(0.10, 0.07, 0.06, C.shellHi), { t: [0, D.shoulderY + 0.015, -D.shoulderZ] }),
      G.xform(G.box(0.10, 0.07, 0.06, C.shellHi), { t: [0, D.shoulderY + 0.015, D.shoulderZ] })
    ])));

    /* ---- head: yaw ---- */
    add(P('head', 'torso', [0, D.neck, 0], G.merge([
      G.xform(G.cyl(0.03, 0.03, 0.03, 6, C.dark), { t: [0, -0.01, 0] }),
      G.xform(G.box(0.115, D.headH, 0.115, C.shellHi), { t: [0, D.headH * 0.5 + 0.01, 0] }),
      G.xform(G.box(0.02, 0.075, 0.10, C.shellLo), { t: [-0.058, D.headH * 0.5 + 0.01, 0] }),
      // visor, facing +X
      G.xform(G.box(0.006, 0.045, 0.085, C.dark), { t: [0.059, D.headH * 0.55, 0] }),
      G.xform(G.box(0.004, 0.028, 0.07, C.visor), { t: [0.062, D.headH * 0.55, 0] }),
      // ear pods
      G.xform(G.cyl(0.022, 0.022, 0.02, 6, C.orange), { t: [0, D.headH * 0.55, -0.06], r: [Math.PI / 2, 0, 0] }),
      G.xform(G.cyl(0.022, 0.022, 0.02, 6, C.orange), { t: [0, D.headH * 0.55, 0.06], r: [Math.PI / 2, 0, 0] })
    ])));

    /* ---- arms: shoulder (pitch) -> arm (abduction) -> hand (elbow) ----
       Three servos, three joints, so the chain has to be three deep: the
       shoulder is the ball the whole arm swings on, the arm's own servo is the
       abduction hinge *at that same point* (so the arm bone hangs from the
       shoulder, not from a second joint), and the elbow starts the forearm.
       Keeping the same part names means the sliders and the tests keep working
       whichever servo is which. */
    for (const [side, z] of [['L', -1], ['R', 1]]) {
      add(P('shoulder' + side, 'torso', [0, D.shoulderY, z * D.shoulderZ], G.merge([
        jointBall(0.032)
      ])));
      add(P('arm' + side, 'shoulder' + side, [0, 0, 0], G.merge([
        bone(0.055, 0.05, 0.055, 0.05, D.upper, C.shell)
      ])));
      add(P('hand' + side, 'arm' + side, [0, -D.upper, 0], G.merge([
        jointBall(0.026),
        bone(0.048, 0.042, 0.048, 0.042, D.fore, C.shellLo),
        G.xform(G.box(0.05, D.hand, 0.042, C.shellHi), { t: [0, -D.fore - D.hand * 0.5, 0] }),
        G.xform(G.box(0.052, 0.022, 0.044, C.orange), { t: [0.002, -D.fore - D.hand * 0.82, 0] })
      ])));
    }

    /* ---- legs: hip (abduction) -> thigh -> calf -> ankle -> foot ---- */
    for (const [side, z] of [['L', -1], ['R', 1]]) {
      add(P('hip' + side, 'pelvis', [0, -0.02, z * D.hipZ], G.merge([
        jointBall(0.03),
        G.xform(G.box(0.055, 0.05, 0.05, C.dark), { t: [0, -0.01, 0] })
      ])));
      add(P('thigh' + side, 'hip' + side, [0, 0, 0], G.merge([
        bone(0.072, 0.062, 0.072, 0.062, D.thigh, C.shell)
      ])));
      add(P('calf' + side, 'thigh' + side, [0, -D.thigh, 0], G.merge([
        jointBall(0.03),
        bone(0.06, 0.05, 0.06, 0.05, D.calf, C.shellHi)
      ])));
      add(P('ankle' + side, 'calf' + side, [0, -D.calf, 0], G.merge([
        G.xform(G.box(0.052, D.ankleH, 0.055, C.dark), { t: [0, -D.ankleH * 0.5, 0] })
      ])));
      add(P('foot' + side, 'ankle' + side, [0.012, -D.ankleH, 0], G.merge([
        G.xform(G.box(D.footLen, D.footH, D.footW, C.shellLo), { t: [0, 0, 0] }),
        G.xform(G.box(0.045, 0.03, D.footW * 0.9, C.orange), { t: [D.footLen * 0.42, 0.002, 0] }),
        G.xform(G.box(D.footLen * 0.95, 0.012, D.footW * 0.96, C.rubber), { t: [0, -0.022, 0] })
      ])));
    }

    return parts;
  }

  /* ---------------------------------------------------------------- posing */
  /* Raw counts in, rotations out. `st.joints` is {name: raw}; anything missing
     stays at its neutral, so a partial pose is legal and does not fling a limb. */
  const axisVec = { x: 0, y: 1, z: 2 };

  function pose(byName, t, phase, st) {
    const p = (st && st.joints) || null;
    for (const j of JOINTS) {
      const part = byName[j.part];
      if (!part) continue;
      const raw = p && p[j.name] != null ? p[j.name] : j.neutral;
      const deg = (raw - j.neutral) * j.sign * D.degPerCount;
      part.rot[0] = part.rot[1] = part.rot[2] = 0;
      part.rot[axisVec[j.axis]] += deg * DEG;
    }

    // A K1 never stands perfectly still; the head drifts a little even when
    // every servo is parked. Real motion, small enough not to look like noise.
    if (!st || st.idle !== false) {
      const head = byName.head;
      head.rot[1] += Math.sin(t * 0.6) * 0.035;
      head.rot[0] += Math.sin(t * 0.43 + 1.1) * 0.012;
    }
  }

  /* How far one leg's pitch servos turn the leg as a whole, in radians.
     Thigh, calf and ankle are all z-axis servos in series, so for rotations
     about parallel axes the angles simply add; the foot is a yaw servo and
     does not pitch. The hip's roll is only a few degrees at most and is
     ignored: this is a display measurement, not a control law. */
  function legPitch(parts, side) {
    let sum = 0;
    for (const n of ['thigh' + side, 'calf' + side, 'ankle' + side, 'foot' + side]) {
      const p = parts[n];
      if (p) sum += p.rot[2];
    }
    return sum;
  }

  global.K1 = {
    D, C, JOINTS, byJoint, PRESETS, neutralPose, clampRaw, buildParts, pose,
    ACTION_DIR, actionDir, actionRaw, actionPose, legPitch,
    degPerCount: D.degPerCount
  };
})(window);
