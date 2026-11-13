/* k1test.js - the rig, not the renderer.

   The K1 model exists to answer one question: "what does this raw count do to
   this servo?" So the things worth testing are the mapping, not the pixels:

     1. the table is complete and matches robot.json's indices and travel
     2. at neutral the robot stands on the floor, mirror-symmetric
     3. moving one joint moves its part and its children, and nothing else
     4. counts clamp to the servo's own travel
     5. an equal delta on a mirrored pair produces a mirror image
     6. the on-screen panel is built from the same table (no second list)

   The axis and sign per joint are a hypothesis; these tests cannot prove them
   right against hardware, only that the model is internally consistent. */
const fs = require('fs'), path = require('path'), vm = require('vm');
const { makeSandbox } = require('./mockenv.js');

const SRC = path.join(__dirname, '..', 'src');
let bad = 0;
const check = (name, cond, detail) => {
  if (cond) console.log('  ok   ' + name);
  else { bad++; console.log('  FAIL ' + name + (detail ? '  <- ' + detail : '')); }
};

/* ------------------------------------------------------------------ 1. table */
global.window = global;
for (const f of ['math.js', 'geo.js', 'k1.js', 'actions.js']) {
  vm.runInThisContext(fs.readFileSync(path.join(SRC, f), 'utf8'), { filename: f });
}
const { M4, K1 } = global;
const JOINTS = K1.JOINTS;

{
  check('17 joints, one per servo', JOINTS.length === 17, JOINTS.length + ' joints');
  const idx = JOINTS.map((j) => j.index).sort((a, b) => a - b);
  check('indices are exactly 0..16 with no duplicates',
    idx.every((v, i) => v === i), idx.join(','));
  check('every joint names a distinct part',
    new Set(JOINTS.map((j) => j.part)).size === 17);
  check('every travel range contains its own neutral',
    JOINTS.every((j) => j.min <= j.neutral && j.neutral <= j.max && j.min < j.max),
    JOINTS.filter((j) => !(j.min <= j.neutral && j.neutral <= j.max && j.min < j.max)).map((j) => j.name).join(','));
  check('only rightArm sits on a stop at neutral (it has no travel below 34)',
    JOINTS.filter((j) => j.neutral === j.min || j.neutral === j.max).map((j) => j.name).join() === 'rightArm',
    JOINTS.filter((j) => j.neutral === j.min || j.neutral === j.max).map((j) => j.name).join());
  check('every axis is one of x/y/z', JOINTS.every((j) => 'xyz'.includes(j.axis) && j.axis.length === 1));
  check('every sign is +1 or -1', JOINTS.every((j) => j.sign === 1 || j.sign === -1));
  check('one count is 0.90 degrees, the scale the card actions are written in',
    K1.degPerCount === 0.9, K1.degPerCount + ' deg/count');
  check('mirrored pairs carry the mirror-mount signs (opposite on pitch, same on roll/yaw)',
    JOINTS.every((j) => {
      if (!j.name.startsWith('right')) return true;
      const l = K1.byJoint['left' + j.name.slice(5)];
      if (!l) return true;
      return j.axis === 'z' ? j.sign === -l.sign : j.sign === l.sign;
    }));
  check('paired neutrals really are mirror-mounted (|left + right - 249| <= 12)',
    JOINTS.filter((j) => j.name.startsWith('right'))
      .every((r) => {
        const l = K1.byJoint['left' + r.name.slice(5)];
        return !l || Math.abs(l.neutral + r.neutral - 249) <= 12;
      }),
    JOINTS.filter((j) => j.name.startsWith('right')).map((r) => {
      const l = K1.byJoint['left' + r.name.slice(5)];
      return l ? l.name + '=' + (l.neutral + r.neutral) : '';
    }).filter(Boolean).join(' '));
}

/* ----------------------------------------------------------------- 2. build */
const parts = K1.buildParts();
const byName = {};
for (const p of parts) byName[p.name] = p;

{
  check('every joint has geometry on the model', JOINTS.every((j) => byName[j.part]));
  check('every part hangs off a part that exists',
    parts.every((p) => p.parent === null || byName[p.parent]));
  /* shoulder, arm and hand are three servos on one limb, so the chain is
     three deep: the abduction hinge sits on the shoulder ball, and the elbow
     starts the forearm a whole upper-arm below it. */
  check('the arm is a three-servo chain: shoulder -> arm -> hand',
    byName.armL.parent === 'shoulderL' && byName.handL.parent === 'armL' &&
    byName.armR.parent === 'shoulderR' && byName.handR.parent === 'armR' &&
    byName.armL.pos.every((v) => v === 0) &&
    Math.abs(byName.handL.pos[1] + K1.D.upper) < 1e-9,
    'armL at ' + byName.armL.pos.join(',') + ' handL at ' + byName.handL.pos.join(','));
  let nan = 0, oor = 0;
  for (const p of parts) {
    for (const v of p.geo.pos) if (!Number.isFinite(v)) nan++;
    for (const v of p.geo.nrm) if (!Number.isFinite(v)) nan++;
    if (p.geo.nrm.length !== p.geo.pos.length) nan++;
    const nv = p.geo.pos.length / 3;
    for (const i of p.geo.idx) if (i < 0 || i >= nv || !Number.isInteger(i)) oor++;
  }
  check('geometry is finite and every index is in range', nan === 0 && oor === 0, nan + ' bad values, ' + oor + ' bad indices');
}

/* ------------------------------------------------------- pose -> world maths */
const tmp = M4.create(), v4 = new Float32Array(4);
const J = (over) => Object.assign(K1.neutralPose(), over || {});
function world(joints) {
  K1.pose(byName, 0, 0, { joints, idle: false });
  for (const p of parts) {
    M4.compose(tmp, p.pos[0], p.pos[1], p.pos[2], p.rot[0], p.rot[1], p.rot[2], null);
    if (p.parent === null) p.world.set(tmp);
    else M4.mul(p.world, byName[p.parent].world, tmp);
  }
}
function verts(name) {
  const out = [], g = byName[name].geo, w = byName[name].world;
  for (let i = 0; i < g.pos.length; i += 3) {
    M4.xformPoint(v4, w, g.pos[i], g.pos[i + 1], g.pos[i + 2]);
    out.push([v4[0], v4[1], v4[2]]);
  }
  return out;
}
function point(part, x, y, z) {
  M4.xformPoint(v4, byName[part].world, x, y, z);
  return [v4[0], v4[1], v4[2]];
}
const lowestSole = () => Math.min(...['footL', 'footR'].map((n) => Math.min(...verts(n).map((a) => a[1]))));
const midpoint = (part) => {
  const v = verts(part);
  return [0, 1, 2].map((i) => v.reduce((a, b) => a + b[i], 0) / v.length);
};
const descendants = (name) => {
  const out = new Set([name]);
  let grew = true;
  while (grew) {
    grew = false;
    for (const p of parts) {
      if (!out.has(p.name) && p.parent && out.has(p.parent)) { out.add(p.name); grew = true; }
    }
  }
  return out;
};

/* neutral: soles on the floor, everything above it, mirror-symmetric */
world(J());
{
  const soles = ['footL', 'footR'].map((n) => Math.min(...verts(n).map((a) => a[1])));
  check('both soles rest on the floor at neutral (0..5 mm)',
    soles.every((y) => y >= 0 && y <= 0.005), soles.map((y) => (y * 1000).toFixed(2) + 'mm').join(' / '));

  let lowest = 9, highest = -9;
  for (const p of parts) for (const a of verts(p.name)) { lowest = Math.min(lowest, a[1]); highest = Math.max(highest, a[1]); }
  check('nothing sinks below the floor at neutral', lowest >= -1e-6, (lowest * 1000).toFixed(2) + 'mm');
  check('the model is about a metre tall (0.85..1.05)', highest > 0.85 && highest < 1.05, highest.toFixed(3));

  for (const j of JOINTS) {
    const comp = { x: 0, y: 1, z: 2 }[j.axis];
    const r = byName[j.part].rot;
    if (r[comp] !== 0) { check('neutral leaves ' + j.name + ' unrotated', false, 'rot=' + r.join(',')); }
  }
  check('neutral is the table neutral for all 17 joints',
    JOINTS.every((j) => K1.neutralPose()[j.name] === j.neutral));
}

/* --------------------------------------- 3. one joint moves one subtree only */
{
  const base = J();
  world(base);
  const rest = parts.map((p) => Float32Array.from(p.world));
  let wrongPart = 0, missedChild = 0, movedSibling = 0;
  for (const j of JOINTS) {
    const d = j.neutral + 1 <= j.max ? +1 : -1;
    world(Object.assign({}, base, { [j.name]: j.neutral + d }));
    const mine = descendants(j.part);
    parts.forEach((p, i) => {
      const changed = !p.world.every((x, k) => x === rest[i][k]);
      if (mine.has(p.name) && !changed) missedChild++;
      if (!mine.has(p.name) && changed) movedSibling++;
    });
    if (byName[j.part].rot[{ x: 0, y: 1, z: 2 }[j.axis]] === 0) wrongPart++;
  }
  check('nudging a joint rotates its own part', wrongPart === 0, wrongPart + ' joints did nothing');
  check('nudging a joint leaves every unrelated part exactly where it was', movedSibling === 0, movedSibling + ' unrelated parts moved');
  check('nudging a joint carries its children with it', missedChild === 0, missedChild + ' children stayed put');
  world(J());
}

/* ------------------------------------------------------------- 4. clamping */
{
  const t = [
    ['leftThigh', 1000, 229], ['leftThigh', 0, 29], ['leftThigh', 128.6, 129],
    ['head', 1000, 202], ['head', -5, 42], ['rightHip', 0, 119]
  ];
  check('counts clamp to each servo\'s own travel',
    t.every(([n, raw, want]) => K1.clampRaw(n, raw) === want),
    t.filter(([n, raw, want]) => K1.clampRaw(n, raw) !== want).map(([n, raw]) => n + '(' + raw + ')=' + K1.clampRaw(n, raw)).join(' '));
  let threw = false;
  try { K1.clampRaw('leftElbow', 100); } catch (e) { threw = true; }
  check('an unknown joint name is a loud error, not a silent no-op', threw);
}

/* --------------------------------------------------------- 5. mirror pairs */
{
  const pairs = JOINTS
    .filter((j) => j.name.startsWith('right'))
    .map((r) => [K1.byJoint['left' + r.name.slice(5)], r])
    .filter(([l]) => l);
  check('there are eight mirrored pairs plus the head', pairs.length === 8, pairs.length + ' pairs');

  /* Symmetric motion is LEFT +d, RIGHT -d: the two servos are the same part
     mounted mirror-image, which is why the neutrals sum to ~249. */
  let worst = 0, worstName = '';
  for (const [l, r] of pairs) {
    const posCap = Math.min(l.max - l.neutral, r.neutral - r.min);
    const negCap = Math.min(l.neutral - l.min, r.max - r.neutral);
    const d = posCap >= 10 ? 10 : posCap >= 1 ? posCap : -Math.min(10, negCap);
    world(J({ [l.name]: l.neutral + d, [r.name]: r.neutral - d }));
    /* vertex *order* is not mirrored (a box lists its +Z face before -Z), so
       match each mirrored point to its nearest twin rather than by index */
    const a = verts(l.part), b = verts(r.part);
    let e = 0;
    for (const p of a) {
      let best = 1e9;
      for (const q of b) best = Math.min(best, Math.hypot(q[0] - p[0], q[1] - p[1], q[2] + p[2]));
      e = Math.max(e, best);
    }
    if (e > worst) { worst = e; worstName = l.name.replace('left', '') + ' d=' + d; }
  }
  check('opposite deltas on a mirrored pair are a mirror image (< 0.1 mm)',
    worst < 1e-4, (worst * 1000).toFixed(3) + 'mm worst on ' + worstName);
  world(J());
}

/* ------------------------------------------------------------- 5b. presets */
{
  const PRESETS = K1.PRESETS;
  const pairs = JOINTS
    .filter((j) => j.name.startsWith('right'))
    .map((r) => [K1.byJoint['left' + r.name.slice(5)], r])
    .filter(([l]) => l);
  const keys = Object.keys(PRESETS);
  check('there are six presets, each with a label', keys.length === 6 && keys.every((k) => PRESETS[k].name));
  check('no preset sends a count outside a servo\'s travel',
    keys.every((k) => Object.keys(PRESETS[k].raw).every((n) => {
      const j = K1.byJoint[n], v = j.neutral + PRESETS[k].raw[n];
      return j && v >= j.min && v <= j.max;
    })),
    keys.filter((k) => Object.keys(PRESETS[k].raw).some((n) => {
      const j = K1.byJoint[n], v = j.neutral + PRESETS[k].raw[n];
      return j && (v < j.min || v > j.max);
    })).join(' '));
  check('a preset never leaves a named servo at a stop (the travel edge is a bad demo)',
    keys.every((k) => Object.keys(PRESETS[k].raw).every((n) => {
      const j = K1.byJoint[n], v = j.neutral + PRESETS[k].raw[n];
      return v > j.min && v < j.max;
    })));

  /* every preset that touches both halves of a pair uses the mirror-mount
     relation: delta(right) === -delta(left) */
  check('presets write symmetric pairs as left +d / right -d',
    keys.every((k) => JOINTS.every((r) => {
      if (!r.name.startsWith('right')) return true;
      const l = K1.byJoint['left' + r.name.slice(5)];
      if (!l) return true;
      const a = PRESETS[k].raw[l.name], b = PRESETS[k].raw[r.name];
      if (a == null && b == null) return true;
      return a != null && b != null && b === -a;
    })),
    keys.filter((k) => !JOINTS.every((r) => {
      if (!r.name.startsWith('right')) return true;
      const l = K1.byJoint['left' + r.name.slice(5)];
      if (!l) return true;
      const a = PRESETS[k].raw[l.name], b = PRESETS[k].raw[r.name];
      if (a == null && b == null) return true;
      return a != null && b != null && b === -a;
    })).join(' '));

  function apply(key) {
    const J = K1.neutralPose();
    for (const n of Object.keys(PRESETS[key].raw)) J[n] = K1.clampRaw(n, K1.byJoint[n].neutral + PRESETS[key].raw[n]);
    return J;
  }
  function mirrorError(key) {
    world(apply(key));
    let e = 0;
    for (const [l, r] of pairs) {
      if (PRESETS[key].raw[l.name] == null) continue;
      const a = verts(l.part), b = verts(r.part);
      for (const p of a) {
        let best = 1e9;
        for (const q of b) best = Math.min(best, Math.hypot(q[0] - p[0], q[1] - p[1], q[2] + p[2]));
        e = Math.max(e, best);
      }
    }
    return e;
  }
  const bad = keys.filter((k) => mirrorError(k) >= 1e-4);
  check('every preset that moves a pair is a true mirror image (< 0.1 mm)', bad.length === 0,
    bad.map((k) => k + ' ' + (mirrorError(k) * 1000).toFixed(2) + 'mm').join(', '));

  /* what each preset is for: check the pose does the thing the label says */
  world(K1.neutralPose());
  const nHandL = midpoint('handL'), nHandR = midpoint('handR'), nVisor = point('head', 0.062, 0.0715, 0);
  const nSole = lowestSole();

  world(apply('lookLeft'));
  check('look left turns the visor towards the robot\'s left (-Z)',
    point('head', 0.062, 0.0715, 0)[2] < nVisor[2] - 0.01,
    'visor dz ' + (point('head', 0.062, 0.0715, 0)[2] - nVisor[2]).toFixed(3));

  world(apply('armsOut'));
  const oHandL = midpoint('handL'), oHandR = midpoint('handR');
  check('arms out moves both hands sideways, away from the body',
    oHandL[2] < nHandL[2] - 0.10 && oHandR[2] > nHandR[2] + 0.10,
    'dz L ' + (oHandL[2] - nHandL[2]).toFixed(3) + ' R ' + (oHandR[2] - nHandR[2]).toFixed(3));

  world(apply('elbows'));
  const tip = (p) => point(p, 0, -K1.D.fore - K1.D.hand, 0);
  const eTipL = tip('handL'), eTipR = tip('handR');
  check('elbows moves both hands forwards (+X)',
    eTipL[0] > 0.10 && eTipR[0] > 0.10,
    'tip dx L ' + eTipL[0].toFixed(3) + ' R ' + eTipR[0].toFixed(3));

  world(apply('crouch'));
  check('crouch lifts the feet relative to the pelvis by > 40 mm (the support solve then plants them)',
    lowestSole() - nSole > 0.04, ((lowestSole() - nSole) * 1000).toFixed(1) + 'mm');
  /* A squat's counts nearly cancel: thigh 80 forward, knee 90 back, ankle 30
     down again. That cancellation is what keeps the torso upright whatever
     degPerCount is, and it is a sign test - get the calf's sign wrong and the
     three angles add up instead. The support solve then tips the body by the
     residual, which is the small forward lean a real squat has. */
  const lpL = K1.legPitch(byName, 'L'), lpR = K1.legPitch(byName, 'R');
  check('a crouch nearly cancels its own leg angles, so the torso stays upright',
    Math.abs(lpL) < 0.53 && Math.abs(lpL - lpR) < 1e-9,
    (lpL * 180 / Math.PI).toFixed(1) + 'deg / ' + (lpR * 180 / Math.PI).toFixed(1) + 'deg');

  world(apply('hipRoll'));
  check('hip roll actually tilts the hips', Math.abs(byName.hipL.rot[0]) > 0.02,
    'hipL rx ' + byName.hipL.rot[0].toFixed(4));
  world(J());
}

/* ------------------------------------------------------- 5c. card actions */
/* The action data is decoded from the robot's own SD card, so these checks are
   about honesty: every keyframe comes from the bytes, every sample stays inside
   a servo's travel, and the direction guess agrees with the presets it is
   supposed to match. They cannot prove the direction against hardware. */
{
  const A = global.K1ACTIONS;
  const keys = A ? Object.keys(A) : [];
  const a = A && A.pushups;

  check('the card decoder produced at least one action', keys.length >= 1, keys.join(','));
  check('push ups is decoded from ProAction, not invented',
    !!a && a.source === 'ProAction/Push Ups.sh', a ? a.source : 'missing');

  if (a) {
    check('push ups is 18 keyframes: the file\'s 12 with its marked pump block played 3x',
      a.frames.length === 18, a.frames.length + ' frames');
    check('the repeat is declared, and it is the block byte 29 marks',
      !!a.repeat && a.repeat.times === 3 &&
      a.repeat.core.join(',') === '4,5,6',
      a.repeat ? JSON.stringify(a.repeat) : 'none');
    if (a.repeat) {
      /* the expansion must be a pure repeat: frames 4..6, 7..9, 10..12 identical */
      const core = a.frames.slice(4, 7).map((f) => JSON.stringify(f.mag));
      const same = [7, 10].every((s) => a.frames.slice(s, s + 3).map((f) => JSON.stringify(f.mag)).join() === core.join());
      check('the three pump blocks are the same three poses', same);
      check('the elbow dips three times (40-100-40, 40-100-40, 40-100-40)',
        a.frames.slice(4, 13).map((f) => f.mag.leftHand).join(',') === '40,100,40,40,100,40,40,100,40',
        a.frames.slice(4, 13).map((f) => f.mag.leftHand).join(','));
    }

    check('the clock is the 9.625 s measured on the robot, not the declared 9.6 s',
      a.duration_ms === 9625 && a.clock === 'measured' && a.declared === true,
      a.duration_ms + 'ms clock=' + a.clock + ' declared=' + a.declared);
    check('keyframe times start at 0, increase, and end exactly on the clock',
      a.frames[0].t === 0 && a.frames[a.frames.length - 1].t === a.duration_ms / 1000 &&
      a.frames.every((f, i) => i === 0 || f.t > a.frames[i - 1].t),
      a.frames.map((f) => f.t).join(','));
    check('every keyframe carries all 17 servos as one byte',
      a.frames.every((f) => JOINTS.every((j) => Number.isInteger(f.mag[j.name]) && f.mag[j.name] >= 0 && f.mag[j.name] <= 255)));

    /* the file starts and ends neutral, which is what makes it safe to play */
    const n = K1.neutralPose();
    const same = (p) => JOINTS.every((j) => p[j.name] === n[j.name]);
    check('the action starts at neutral and returns to neutral',
      same(K1.actionRaw(a.frames[0])) && same(K1.actionRaw(a.frames[a.frames.length - 1])));
    check('sampling past the end holds the last frame, and before the start the first',
      same(K1.actionPose(a, 99)) && same(K1.actionPose(a, -5)));

    /* every sample, not just the keyframes, inside the servo travel */
    let oor = 0, samples = 0, worst = null;
    for (let i = 0; i <= 400; i++) {
      const p = K1.actionPose(a, (a.duration_ms / 1000) * i / 400);
      samples++;
      for (const j of JOINTS) {
        if (!(p[j.name] >= j.min && p[j.name] <= j.max)) { oor++; worst = j.name + '=' + p[j.name]; }
      }
    }
    check('no sample leaves a servo\'s travel (' + samples + ' samples)', oor === 0, worst || '');

    /* the file overshoots: 110 counts of thigh against 100 of travel */
    check('a magnitude larger than the travel clamps to the stop instead of wrapping',
      K1.actionRaw(a.frames[1]).leftThigh === K1.byJoint.leftThigh.max &&
      K1.actionRaw(a.frames[1]).rightThigh === K1.byJoint.rightThigh.min,
      'left ' + K1.actionRaw(a.frames[1]).leftThigh + ' right ' + K1.actionRaw(a.frames[1]).rightThigh);

    /* When the file gives a pair the same magnitude, the pose must come out
       symmetric (opposite raw deltas). Some frames do carry different numbers
       per side - 103/100 on the thighs, 25/30 on the calves - and those are the
       file's own asymmetry, not the model's. */
    let asym = 0, equalPairs = 0;
    for (const f of a.frames) {
      for (const j of JOINTS) {
        if (!j.name.startsWith('right')) continue;
        const l = K1.byJoint['left' + j.name.slice(5)];
        if (!l || f.mag[l.name] !== f.mag[j.name]) continue;
        equalPairs++;
        const dl = K1.actionRaw(f)[l.name] - l.neutral;
        const dr = K1.actionRaw(f)[j.name] - j.neutral;
        if (dl !== -dr) asym++;
      }
    }
    check('an equal left/right magnitude always becomes a symmetric pose (' + equalPairs + ' such pairs)',
      asym === 0 && equalPairs > 0, asym + ' asymmetric');

    /* it moves, and it moves the parts a push-up moves */
    let reach = {}, peak = {};
    for (const j of JOINTS) { reach[j.name] = 0; peak[j.name] = 0; }
    for (let i = 0; i <= 400; i++) {
      const p = K1.actionPose(a, (a.duration_ms / 1000) * i / 400);
      for (const j of JOINTS) reach[j.name] = Math.max(reach[j.name], Math.abs(p[j.name] - j.neutral));
    }
    check('push ups bends both legs a long way (> 90 counts)',
      reach.leftThigh > 90 && reach.rightThigh > 90,
      'left ' + reach.leftThigh + ' right ' + reach.rightThigh);
    check('push ups pumps both arms at the elbow (> 30 counts)',
      reach.leftHand > 30 && reach.rightHand > 30 && reach.leftArm > 5,
      'hands ' + reach.leftHand + '/' + reach.rightHand + ' arms ' + reach.leftArm + '/' + reach.rightArm);
    check('push ups leaves the head and the tiny hip/foot servos alone',
      reach.head === 0 && reach.leftHip === 0 && reach.rightHip === 0,
      'head ' + reach.head + ' hips ' + reach.leftHip + '/' + reach.rightHip);

    /* The pump block stores 0 on both thighs. That is not a stand-up in the
       middle of the action: a robot lying face down has its legs straight, and
       straight is the thigh's neutral. The body being on the floor is what
       `contact: "hands"` tells the display. */
    const held = K1.actionPose(a, a.frames[4].t);
    check('the pump block leaves the thighs straight, as a prone robot has them',
      held.leftThigh === K1.byJoint.leftThigh.neutral &&
      held.rightThigh === K1.byJoint.rightThigh.neutral && !!a.contact,
      'thigh ' + (held.leftThigh - K1.byJoint.leftThigh.neutral) + ' contact ' + a.contact);

    /* --- the direction guess, checked for internal consistency --- */
    const pairsMirror = JOINTS.filter((j) => j.name.startsWith('right')).every((r) => {
      const l = 'left' + r.name.slice(5);
      return K1.actionDir(l) === -K1.actionDir(r.name);
    });
    check('the direction table mirrors every pair and points the head one way',
      pairsMirror && Math.abs(K1.actionDir('head')) === 1);

    /* The hip guess is checkable by name: `Open Legs` stores 20 on both hip
       servos and the card parks them at 10 in almost every other action. An
       inverted sign drew the feet together instead, so Celebrate crossed its
       legs while the real robot spreads them. */
    world(K1.neutralPose());
    const stance = midpoint('footR')[2] - midpoint('footL')[2];
    const stanceAt = (key, t) => {
      world(K1.actionPose(A[key], t));
      return midpoint('footR')[2] - midpoint('footL')[2];
    };
    const openGap = stanceAt('openlegs', 0);
    check('Open Legs widens the stance instead of crossing the feet',
      openGap > stance + 0.05,
      (openGap * 1000).toFixed(0) + 'mm vs ' + (stance * 1000).toFixed(0) + 'mm neutral');
    let celMin = Infinity;
    for (const f of A.celebrate.frames) celMin = Math.min(celMin, stanceAt('celebrate', f.t));
    check('Celebrate spreads the legs at every keyframe',
      celMin > stance + 0.05,
      (celMin * 1000).toFixed(0) + 'mm narrowest vs ' + (stance * 1000).toFixed(0) + 'mm neutral');

    /* The leg bend must agree with CROUCH, because that preset was the one
       checked against a human leg: knees flex, feet stay flat-ish. If the thigh
       direction were the other way, the hip and knee would fold in opposition. */
    /* What the bytes actually say, rather than what a push-up "should" be: at
       the deepest frame the thighs travel 110 counts with the CALVES AT ZERO,
       so this is a hinge at the hips, not a squat. The knees do bend later.
       Worth asserting, because a decoder that had the byte order wrong would
       put the 110 on a different servo. */
    world(K1.neutralPose());
    const deep = K1.actionRaw(a.frames[1]);
    world(K1.actionPose(a, a.frames[1].t));
    check('the deepest frame hinges at the hip with straight knees (calf = 0, as in the file)',
      deep.leftCalf === K1.byJoint.leftCalf.neutral && deep.rightCalf === K1.byJoint.rightCalf.neutral &&
      reach.leftThigh > 90,
      'calf ' + (deep.leftCalf - K1.byJoint.leftCalf.neutral) + ' thigh ' + (deep.leftThigh - K1.byJoint.leftThigh.neutral));
    /* At 0.9 deg/count a 100-count thigh is a right angle, so the knee must be
       a thigh-length in front of the hip. That is the check that the scale is
       the card's, not a decorative fudge: at 0.40 the thigh would only reach
       40 degrees and the whole action would be a shallow bow. */
    const hip1 = point('hipL', 0, 0, 0), knee1 = point('calfL', 0, 0, 0);
    check('the thigh reaches horizontal, a whole thigh-length ahead of the hip',
      knee1[0] - hip1[0] > 0.16, 'knee ahead by ' + (knee1[0] - hip1[0]).toFixed(3) + ' m');
    world(J());

    /* the calves are used elsewhere in the cycle, so the decoder is not simply
       ignoring that slot */
    const calfReach = Math.max(...Array.from({ length: 401 }, (_, i) =>
      Math.abs(K1.actionPose(a, (a.duration_ms / 1000) * i / 400).leftCalf - K1.byJoint.leftCalf.neutral)));
    check('the knees do bend later in the cycle', calfReach > 20, 'max calf travel ' + calfReach);

    /* the arm pump folds the elbow forwards, the way ELBOWS does, not backwards.
       Measured from the shoulder, because the whole body pitches in this pose. */
    world(K1.neutralPose());
    const sh0 = point('shoulderL', 0, 0, 0), hp0 = midpoint('handL');
    world(K1.actionPose(a, a.frames[4].t));      // the 100-count elbow beat
    const sh4 = point('shoulderL', 0, 0, 0), hp4 = midpoint('handL');
    check('the arm pump folds the elbow forwards (+X), like the ELBOWS preset',
      hp4[0] - sh4[0] > (hp0[0] - sh0[0]) + 0.04,
      'hand ahead of shoulder by ' + (hp4[0] - sh4[0]).toFixed(3) + ' (neutral ' + (hp0[0] - sh0[0]).toFixed(3) + ')');
    world(J());
  }
}

/* ------------------------------------------------- 5d. the whole move menu */
/* src/actions.js is generated from the phone's move list, so these checks are
   about that generation being complete and honest: every move the phone can
   play is here, a missing script is listed but not playable, and a measured
   clock is the one baked into the frames. */
{
  const A = global.K1ACTIONS || {};
  const list = global.K1ACTION_LIST || [];
  const keys = Object.keys(A);
  check('the browser lists 90 moves: 68 from robot.json plus 22 card scripts',
    list.length === 90 && keys.length === 90, list.length + ' listed, ' + keys.length + ' decoded');
  check('the list and the map agree, with no duplicate keys',
    list.every((k, i) => A[k] && list.indexOf(k) === i) && keys.every((k) => list.indexOf(k) >= 0),
    'missing=' + list.filter((k) => !A[k]).join(','));
  check('keys are stable deep-link slugs (letters and digits only)',
    keys.every((k) => /^[a-z0-9]+$/.test(k)), keys.filter((k) => !/^[a-z0-9]+$/.test(k)).join(','));
  check('every move belongs to one of the phone menu\'s three groups',
    keys.every((k) => ['Card', 'ProAction', 'Action'].indexOf(A[k].folder) >= 0),
    [...new Set(keys.map((k) => A[k].folder))].join(','));

  const playable = keys.filter((k) => A[k].available !== false);
  const missing = keys.filter((k) => A[k].available === false);
  check('86 of the 90 moves have a .sh to decode', playable.length === 86, playable.length + ' playable');
  check('the four actions robot.json names but the card lacks are listed, not invented',
    missing.map((k) => A[k].name).sort().join(',') === 'Easy Action,Funny,Quick sneak,The Squat',
    missing.map((k) => A[k].name).join(','));
  check('a missing script has no frames and says why',
    missing.every((k) => A[k].frames.length === 0 && !!A[k].reason));

  let badFrames = 0, badTimes = 0, badMag = 0;
  for (const k of playable) {
    const a = A[k];
    if (!a.frames.length || a.frames[0].t !== 0) badFrames++;
    if (a.frames[a.frames.length - 1].t !== a.duration_ms / 1000) badTimes++;
    for (const f of a.frames) {
      if (!JOINTS.every((j) => Number.isInteger(f.mag[j.name]) && f.mag[j.name] >= 0 && f.mag[j.name] <= 255)) badMag++;
      if (f.t < 0 || f.t > a.duration_ms / 1000 + 1e-9) badTimes++;
    }
  }
  check('every playable move starts at 0 and ends exactly on its clock', badFrames === 0 && badTimes === 0,
    badFrames + ' bad starts, ' + badTimes + ' bad times');
  check('every keyframe carries all 17 servos as one byte', badMag === 0, badMag + ' bad frames');
  check('every clock says where it came from',
    keys.every((k) => ['measured', 'declared', 'estimated', 'unknown'].indexOf(A[k].clock) >= 0));

  /* Spot checks against the phone data's measured seconds. Play Dead's declared
     1.68 s and its measured 16.78 s are the reason this field exists. */
  const secs = (k) => A[k].duration_ms / 1000;
  check('measured clocks are the hardware ones, not robot.json\'s declaration',
    secs('playdead') === 16.781 && secs('openfire') === 13.907 && secs('sayhello') === 6.453,
    'playdead=' + secs('playdead') + ' openfire=' + secs('openfire') + ' sayhello=' + secs('sayhello'));
  check('the card-only measured clocks survive, and unknown ones are marked estimated',
    secs('lookleft') === 1.375 && secs('sit') === 7.625 && secs('stand') === 10.094 &&
    A.waveleft.clock === 'estimated' && A.showtime.clock === 'estimated',
    'lookleft=' + secs('lookleft') + ' waveleft=' + A.waveleft.clock);

  /* Every sample of every action has to stay inside the servo travel: the
     files overshoot (Push Ups asks for 110 counts of a 100-count thigh). */
  let oor = 0, nan = 0;
  for (const k of playable) {
    const a = A[k];
    for (let i = 0; i <= 60; i++) {
      const p = K1.actionPose(a, (a.duration_ms / 1000) * i / 60);
      for (const j of JOINTS) {
        if (!Number.isFinite(p[j.name])) nan++;
        else if (p[j.name] < j.min || p[j.name] > j.max) oor++;
      }
    }
  }
  check('no sample of any action leaves a servo\'s travel or becomes NaN',
    oor === 0 && nan === 0, oor + ' out of range, ' + nan + ' NaN');
}

/* --------------------------------------------------------------- 6. panel */
{
  const opts = { audio: 'normal' };
  makeSandbox(opts);
  const FILES = ['math.js', 'geo.js', 'gl.js', 'shaders.js', 'k1.js', 'actions.js', 'hud.js', 'music.js', 'main.js'];
  for (const f of FILES) vm.runInContext(fs.readFileSync(path.join(SRC, f), 'utf8'), opts.ctx, { filename: f });

  for (const fn of opts.listeners['intro:click'] || []) fn({});
  const run = (n) => { for (let i = 0; i < n; i++) { const q = opts.raf; opts.raf = []; for (const f of q) f(1000 + i * 16); } };
  run(3);

  const win = vm.runInContext('window', opts.ctx);
  const rows = opts.els['rows'];
  const demo = win.K1DEMO;

  check('the demo booted and exposed its state', win.__K1_BOOTED === true && !!demo);
  check('the panel has one row per servo', rows && rows.children.length === 17,
    rows ? rows.children.length + ' rows' : 'no #rows element');

  /* ---- the move browser: built from the generated phone list ---- */
  {
    const pick = opts.els['movePick'];
    const find = opts.els['moveFind'];
    const flat = () => {
      const out = [];
      for (const g of (pick ? pick.children : [])) {
        for (const o of (g.children || [])) out.push(o);
      }
      return out;
    };
    const optsAll = flat();
    check('the move browser lists every phone move, grouped',
      optsAll.length === 90 && pick.children.length === 3,
      optsAll.length + ' options in ' + (pick ? pick.children.length : 0) + ' groups');
    check('Push Ups is the selected default, so P and the old button still mean it',
      pick && pick.value === 'pushups' && optsAll.some((o) => o.value === 'pushups'));
    check('an action with no script is listed but disabled',
      optsAll.some((o) => o.value === 'funny' && o.disabled === true));
    check('the readout names the measured clock and keyframe count',
      /9\.6[23]s · measured · 18 keyframes/.test(opts.els['moveInfo'].textContent),
      opts.els['moveInfo'].textContent);

    if (find) {
      find.value = 'kick';
      for (const fn of (find._on && find._on.input) || []) fn({});
      const hits = flat().map((o) => o.value).sort().join(',');
      check('the filter narrows the list without inventing entries',
        hits === 'leftkick,rightkick', hits);
      check('the filter keeps a valid selection', pick.value === 'leftkick', pick.value);
      find.value = '';
      for (const fn of (find._on && find._on.input) || []) fn({});
      pick.value = 'pushups';
    }

    const play = opts.els['movePlay'];
    if (play) {
      pick.value = 'lookleft';
      for (const fn of (play._on && play._on.click) || []) fn({});
      check('the Play button starts the selected move with its own clock',
        demo.act.playing === true && demo.act.key === 'lookleft' &&
        demo.act.data.duration_ms === 1375,
        demo.act.key + ' ' + (demo.act.data ? demo.act.data.duration_ms : 'none'));
      const stop = opts.els['moveStop'];
      for (const fn of (stop._on && stop._on.click) || []) fn({});
      check('the Stop button hands the pose back', demo.act.playing === false);
      pick.value = 'pushups';
    }

    /* Music was on by default before this change; the page is a pose rig, so
       silence is the default and M is the explicit switch. */
    check('music is off after the intro click', win.Music.playing === false);
    for (const fn of opts.listeners['window:keydown'] || []) fn({ key: 'm', preventDefault() {} });
    check('M starts the music on demand', win.Music.playing === true);
    for (const fn of opts.listeners['window:keydown'] || []) fn({ key: 'm', preventDefault() {} });
    check('M stops it again', win.Music.playing === false);
  }

  if (rows && demo) {
    let mismatched = 0, wrongBounds = 0, wrongValue = 0;
    rows.children.forEach((row, i) => {
      const j = demo.JOINTS[i];
      const sl = row.children.find((c) => c.type === 'range');
      if (!j || row.dataset.joint !== j.name) mismatched++;
      if (!sl || +sl.min !== j.min || +sl.max !== j.max || +sl.step !== 1) wrongBounds++;
      else if (+sl.value !== j.neutral) wrongValue++;
    });
    check('the rows are the joints, in index order', mismatched === 0, mismatched + ' mismatched');
    check('each slider spans exactly that servo\'s travel', wrongBounds === 0, wrongBounds + ' wrong');
    check('each slider starts at the servo\'s neutral', wrongValue === 0, wrongValue + ' wrong');

    /* the export is what a pose is actually pasted into a routine as */
    const pose = demo.poseJSON();
    check('poseJSON exports all 17 by name', Object.keys(pose.byName).length === 17);
    check('poseJSON exports a dense 17-slot index array',
      pose.byIndex.length === 17 && demo.JOINTS.every((j) => pose.byIndex[j.index] === pose.byName[j.name]));

    demo.applyPreset('crouch');
    check('a preset stays inside every servo limit',
      demo.JOINTS.every((j) => demo.st.target[j.name] >= j.min && demo.st.target[j.name] <= j.max));
    const lt = demo.st.target.leftThigh - K1.byJoint.leftThigh.neutral;
    const rt = demo.st.target.rightThigh - K1.byJoint.rightThigh.neutral;
    check('a preset uses the mirror-mount relation (the thighs move by opposite counts)',
      rt === -lt && lt !== 0, 'left ' + lt + ' right ' + rt);

    demo.selectJoint(16);
    demo.nudge(1000);
    check('nudging the selected joint clamps at the servo stop',
      demo.st.target.head === demo.JOINTS[16].max, 'head=' + demo.st.target.head);

    /* ---- the action player, through the same state the panel drives ---- */
    /* The harness clock is real time, which barely moves between synchronous
       frames; give it 16 ms a frame so the player's own clock advances. */
    const perf = vm.runInContext('performance', opts.ctx);
    let clockMs = perf.now();
    perf.now = () => clockMs;
    const tick = (n) => { for (let i = 0; i < n; i++) { clockMs += 16; run(1); } };

    check('the player knows the decoded action', !!demo.actions().pushups);
    check('an unknown action is refused, not silently played',
      demo.playAction('no-such-stunt') === false);

    demo.applyPreset('neutral');
    const t0 = demo.st.target.leftThigh;
    check('push ups starts from neutral', t0 === K1.byJoint.leftThigh.neutral, 'leftThigh=' + t0);
    check('the push-ups button starts the action', demo.playAction('pushups') === true &&
      demo.act.playing === true && /PUSH UPS/.test(demo.st.scene), demo.st.scene);

    /* 9.625 s at 16 ms frames: run well past the end */
    for (let i = 0; i < 700 && demo.act.playing; i++) tick(1);
    const endT = demo.actions().pushups.duration_ms / 1000;
    check('the action finishes on its own clock and stops',
      demo.act.playing === false && Math.abs(demo.act.t - endT) < 1e-9, 't=' + demo.act.t);
    check('it hands the model back at neutral, where the file ends',
      demo.JOINTS.every((j) => demo.st.target[j.name] === j.neutral),
      demo.JOINTS.filter((j) => demo.st.target[j.name] !== j.neutral).map((j) => j.name).join(','));

    /* moving a slider is the user taking over; the action must yield */
    demo.playAction('pushups');
    tick(3);
    demo.nudge(5);
    check('a manual nudge cancels the action instead of fighting it',
      demo.act.playing === false);
    demo.playAction('pushups');
    tick(3);
    demo.applyPreset('crouch');
    check('a preset click cancels the action', demo.act.playing === false);

    /* ---- the standing support (display only, no servo counts) ---- */
    /* A K1 has no waist servo, so the body must be solved around the foot the
       weight is on: the support sole stays flat and in place, and the pelvis
       tips and slides with the leg chain. */
    demo.applyPreset('neutral');
    tick(30);
    check('at rest the body is not pitched', Math.abs(demo.lean()) < 0.02,
      (demo.lean() * 180 / Math.PI).toFixed(2) + 'deg');
    demo.applyPreset('crouch');
    tick(30);
    check('a crouch keeps the torso upright because its leg counts cancel (75-150+75 = 0)',
      Math.abs(demo.lean()) < 0.04, (demo.lean() * 180 / Math.PI).toFixed(2) + 'deg');
    demo.applyPreset('neutral');
    tick(30);
    const lowOf = (m, n) => m.find((p) => p.name === n).low;
    const refX = demo.measure().find((p) => p.name === 'footL').lowAt[0];
    demo.playAction('pushups');
    tick(Math.round((demo.actions().pushups.frames[1].t / 0.016)));   // the deepest hinge
    check('at the deepest frame the body folds forward over the planted feet (> 110 deg)',
      demo.lean() < -1.9, (demo.lean() * 180 / Math.PI).toFixed(1) + 'deg');

    /* ---- contact: a push-up rests on its palms and toes, not on air ---- */
    /* Every sample is between keyframes, where the pose and the body pitch are
       both interpolated; the palms must still be level with the lowest sole,
       and nothing may go through the floor. This is the check that catches the
       old model, which planted the palms at the keyframes and let them sink
       between them. */
    demo.playAction('pushups');
    const at = (t) => { while (demo.act.playing && demo.act.t < t) tick(1); return demo.measure(); };
    let worstGap = 0, worstT = 0, below = 0, slide = 0;
    const pelvisAt = {};
    /* every keyframe from the first up to the last one that still rests on its
       hands, and every midpoint between two of them: the stand-up that follows
       is supposed to lift the palms. Derived from the decoded clock, so the
       probes follow the action instead of a stale list. */
    const probeFrames = demo.actions().pushups.frames;
    const last = probeFrames.length - 3;
    const idx = [];
    for (let i = 1; i <= last; i++) idx.push(i);
    const probes = idx.map((i) => probeFrames[i].t)
      .concat(idx.map((i) => (probeFrames[i].t + probeFrames[i + 1].t) / 2));
    for (const t of probes) {
      const m = at(t);
      pelvisAt[t] = m.find((p) => p.name === 'pelvis').y;
      const palm = Math.min(lowOf(m, 'handL'), lowOf(m, 'handR'));
      const sole = Math.min(lowOf(m, 'footL'), lowOf(m, 'footR'));
      if (Math.abs(palm - sole) > worstGap) { worstGap = Math.abs(palm - sole); worstT = t; }
      below = Math.min(below, Math.min(...m.map((p) => p.low)));
      /* the support foot is the one under the neutral sole, whichever side the
         solve picked this frame */
      const planted = ['footL', 'footR']
        .map((n) => m.find((p) => p.name === n))
        .filter((p) => p.low < 0.02)
        .map((p) => Math.abs(p.lowAt[0] - refX));
      slide = Math.max(slide, Math.min(...planted));
    }
    check('between keyframes the palms stay level with the lowest sole (<= 2 mm)',
      worstGap <= 0.002, 'worst ' + (worstGap * 1000).toFixed(1) + 'mm at ' + worstT + 's');
    check('no part of the robot goes through the floor during the action',
      below >= -0.001, (below * 1000).toFixed(1) + 'mm');
    check('the foot on the floor stays planted under the body (< 1 cm of slide)',
      slide < 0.01, (slide * 1000).toFixed(1) + 'mm');

    /* The pumps are a body going down and up between straight-armed beats: the
       pelvis has to descend, not just the arms rotate. Check the deepest frame
       of each pump against the straight-armed frame before it - the block is
       played three times now, so there are three dips to check. */
    const rp = demo.actions().pushups.repeat;
    const coreFrames = demo.actions().pushups.frames.slice(rp.core[0], rp.core[0] + rp.core.length * rp.times);
    const deep = coreFrames.filter((_, i) => i % rp.core.length === 1);
    check('the pump block is played three times', deep.length === 3, deep.map((f) => f.t).join(','));
    for (const f of deep) {
      const upT = coreFrames[coreFrames.indexOf(f) - 1].t;
      const dy = pelvisAt[upT] - pelvisAt[f.t];
      check('the pump at ' + f.t.toFixed(2) + 's dips the pelvis (> 2.5 cm down)',
        dy > 0.025, (dy * 100).toFixed(1) + 'cm');
    }
    demo.stopAction();

    /* ---- contact: 'seat' is a floor sit, not a standing solve ---- */
    /* Take A Seat used to solve the standing flat-sole pitch from its leg
       chain: at 1.3 s that pitched the torso 108 deg forward and drove the
       head through the floor, while the seated frame folded the legs behind
       the body. The seat hint must keep the torso upright, lower the body onto
       its lowest part, and keep the feet in front of the pelvis. */
    const seatKeys = ['takeaseat', 'sit', 'stand', 'sitlookleft', 'sitlookright', 'sitshowhand'];
    check('every seated move carries the floor-seat hint',
      seatKeys.every((k) => demo.actions()[k] && demo.actions()[k].contact === 'seat'),
      seatKeys.filter((k) => !demo.actions()[k] || demo.actions()[k].contact !== 'seat').join(','));

    demo.playAction('takeaseat');
    tick(1);
    const seatFrames = demo.actions().takeaseat.frames;
    const seatProbes = [];
    for (let i = 0; i < seatFrames.length; i++) {
      seatProbes.push(seatFrames[i].t);
      if (i + 1 < seatFrames.length) {
        seatProbes.push((seatFrames[i].t + seatFrames[i + 1].t) / 2);
      }
    }
    const atSeat = (t) => {
      while (demo.act.playing && demo.act.t < t) tick(1);
      return demo.measure();
    };
    let seatLean = 0, seatFloor = 0;
    const seatPose = {};
    for (const t of seatProbes.sort((a, b) => a - b)) {
      const m = atSeat(t);
      const lean = demo.lean();
      if (Math.abs(lean) > Math.abs(seatLean)) seatLean = lean;
      seatFloor = Math.min(seatFloor, Math.min(...m.map((p) => p.low)));
      if (Math.abs(t - 1.307) < 1e-6 || Math.abs(t - 4.27) < 1e-6) seatPose[t] = m;
    }
    check('Take A Seat keeps the torso near upright (no past-horizontal pitch)',
      Math.abs(seatLean) < 15 * Math.PI / 180,
      (seatLean * 180 / Math.PI).toFixed(1) + 'deg');
    check('no part of Take A Seat goes through the floor',
      seatFloor >= -0.001, (seatFloor * 1000).toFixed(1) + 'mm');

    const seatPart = (m, n) => m.find((p) => p.name === n);
    const early = seatPose[1.307], seated = seatPose[4.27];
    if (early && seated) {
      const hipX = (m) => seatPart(m, 'pelvis').lowAt[0];
      const footX = (m) => Math.min(seatPart(m, 'footL').lowAt[0], seatPart(m, 'footR').lowAt[0]);
      check('mid-descent the feet are in front of the hips, not folded behind them',
        footX(early) > hipX(early) + 0.15,
        'foot ' + footX(early).toFixed(2) + ' hip ' + hipX(early).toFixed(2));
      check('the seated pelvis rests near the floor',
        seatPart(seated, 'pelvis').low < 0.08,
        (seatPart(seated, 'pelvis').low * 100).toFixed(1) + 'cm');
      check('the seated feet are forward of the pelvis (a floor sit)',
        footX(seated) > hipX(seated) + 0.15,
        'foot ' + footX(seated).toFixed(2) + ' hip ' + hipX(seated).toFixed(2));
    } else {
      check('the seat probe reached the descent and seated keyframes', false);
    }
    demo.stopAction();
  }
}

console.log(bad ? '\nRESULT: FAIL (' + bad + ')' : '\nRESULT: PASS');
process.exit(bad ? 1 : 0);
