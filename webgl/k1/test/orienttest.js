/* orienttest.js - is the image the right way up?
   The whole chain has two orientation traps and neither shows up in a numeric
   walk test, because the walk test never touches the camera or the blit:
     1. world -> clip. A point above the floor must land above a point on the
        floor in NDC, i.e. ndcY must increase with world Y.
     2. clip -> screen. The scene FBO is in GL orientation (no flip); the HUD
        canvas has row 0 at the top, which texImage2D puts at v=0, so it does
        need a flip. One shader serves both, so the choice has to be driven by
     uFlip and set correctly at each call site.
   A single hardcoded flip in the blit renders the entire scene upside down,
   which is exactly the bug this exists to catch. */
const fs = require('fs'), path = require('path'), vm = require('vm');

const SRC = path.join(__dirname, '..', 'src');
const ctx = { window: {} };
vm.createContext(ctx);
for (const f of ['math.js', 'geo.js', 'k1.js']) vm.runInContext(fs.readFileSync(path.join(SRC, f), 'utf8'), ctx, { filename: f });
const { M4, K1 } = ctx.window;
const SH = (() => { const c = { window: {} }; vm.createContext(c); vm.runInContext(fs.readFileSync(path.join(SRC, 'shaders.js'), 'utf8'), c); return c.window.SH; })();

let bad = 0;
const check = (name, cond, detail) => {
  if (cond) console.log('  ok   ' + name);
  else { bad++; console.log('  FAIL ' + name + (detail ? '  <- ' + detail : '')); }
};

const RW = 320, RH = 200;
const f = (x) => (Math.round(x * 1e4) / 1e4);

/* ---- 1. projection keeps the vertical axis pointing up ---- */
{
  const proj = M4.create(), view = M4.create(), vp = M4.create(), invVP = M4.create();
  const eye = [3, 2, 3];
  M4.perspective(proj, 46 * Math.PI / 180, RW / RH, 0.05, 400);
  M4.lookAt(view, eye, [0, 0.9, 0], [0, 1, 0]);
  M4.mul(vp, proj, view);
  M4.invert(invVP, vp);
  if (!invVP[0]) { console.log('  FAIL perspective x view is not invertible'); bad++; }
  const out = new Float32Array(4);
  const ndcY = (x, y, z) => { M4.xformPoint(out, vp, x, y, z); return out[1] / out[3]; };
  const ndcX = (x, y, z) => { M4.xformPoint(out, vp, x, y, z); return out[0] / out[3]; };

  const foot = ndcY(0, 0, 0);
  const head = ndcY(0, 1.7, 0);
  const higher = ndcY(0, 3, 0);
  check('a point higher in the world projects higher on screen', head > foot,
    'foot ' + f(foot) + ' head ' + f(head));
  check('ndcY increases monotonically with world Y', higher > head && foot < head && higher > head,
    foot + ' < ' + head + ' < ' + higher);
  check('ndcY stays in the unit range for scene points', foot > -1.2 && foot < 1.2 && head > -1.2 && head < 1.2,
    'foot ' + f(foot) + ' head ' + f(head));

  // the camera is to the +X+Z side, so +X must land on one side consistently
  check('world +X projects to a definite screen side', Math.abs(ndcX(1, 0.9, 0) - ndcX(0, 0.9, 0)) > 0.01);

  // inverse must round-trip, or the sky rays come out mirrored.
  // unproject with the point's OWN ndc z, not a guessed one.
  const ndcOf = (x, y, z) => { M4.xformPoint(out, vp, x, y, z); return [out[0] / out[3], out[1] / out[3], out[2] / out[3]]; };
  const [bx, by, bz] = ndcOf(0, 0.9, 0);
  const back = new Float32Array(4);
  M4.xformPoint(back, invVP, bx, by, bz);          // w = 1, homogeneous NDC
  const wx = back[0] / back[3], wy = back[1] / back[3], wz = back[2] / back[3];
  check('invVP round-trips a world point',
    Math.abs(wx - 0) < 1e-3 && Math.abs(wy - 0.9) < 1e-3 && Math.abs(wz - 0) < 1e-3,
    f(wx) + ',' + f(wy) + ',' + f(wz));
}

/* ---- 2. the robot itself is built the right way up ---- */
{
  const parts = K1.buildParts();
  const byName = {};
  for (const p of parts) byName[p.name] = p;
  const rootM = M4.create(), tmpM = M4.create();
  // a pose that is not the neutral, so a stale identity matrix cannot pass
  const st = {
    joints: Object.assign(K1.neutralPose(), { leftShoulder: 240, rightShoulder: 60, head: 150 }),
    idle: false, yaw: 0
  };
  K1.pose(byName, 0, 0, st);

  M4.compose(rootM, 0, 0, 0, 0, 0, 0, null);
  const world = [];
  for (const p of parts) {
    M4.compose(tmpM, p.pos[0], p.pos[1], p.pos[2], p.rot[0], p.rot[1], p.rot[2], null);
    if (p.parent === null) M4.mul(p.world, rootM, tmpM);
    else M4.mul(p.world, byName[p.parent].world, tmpM);
    world.push([p.name, p.world]);
  }
  const ys = world.map(([n, m]) => [n, m[13]]);
  const head = ys.find(([n]) => /head/i.test(n));
  const feet = ys.filter(([n]) => /foot/i.test(n));
  check('a head part exists above the feet', head && feet.every(([, y]) => head[1] > y),
    head ? head[0] + ' y=' + f(head[1]) : 'no head part');
  check('no part is below the floor at rest', ys.every(([, y]) => y > -0.02),
    ys.filter(([, y]) => y <= -0.02).map(([n, y]) => n + '=' + f(y)).join(' '));
  // the model's local +Y must stay world +Y: a negative scale would invert it
  const up = world.find(([n]) => /torso|chest|body|hip/i.test(n));
  if (up) {
    const m = up[1];
    const yAxis = [m[4], m[5], m[6]];       // column 1 = local +Y in world
    check('local +Y maps to world +Y (no negative scale)', yAxis[1] > 0.99, 'yAxis=' + yAxis.map(f).join(','));
  }
}

/* ---- 3. the blit shader offers both orientations ---- */
{
  const fs3 = SH.FS_BLIT;
  check('FS_BLIT decides orientation at runtime', /uFlip/.test(fs3),
    'uFlip missing: one hardcoded orientation for two differently-oriented textures');
  check('FS_BLIT flips only when asked', /uFlip\s*>\s*0\.5\s*\?\s*1\.0\s*-\s*vUv\.y\s*:\s*vUv\.y/.test(fs3),
    fs3.split('\n').filter((l) => l.includes('float v')).join(' '));
}

/* ---- 4. each call site passes the right orientation ---- */
{
  const main = fs.readFileSync(path.join(SRC, 'main.js'), 'utf8');
  const minBlit = main.slice(main.indexOf('if (minimal) {'));
  const flips = [...minBlit.matchAll(/uFlip,\s*([01])\)/g)].map((m) => m[1]);
  check('the fallback blit sets uFlip for every draw', flips.length >= 2, 'found ' + flips.length);
  check('the first blit (the scene) is NOT flipped', flips[0] === '0', 'uFlip=' + flips[0]);
  check('the second blit (the HUD) IS flipped', flips[1] === '1', 'uFlip=' + flips[1]);

  // the full post shader has the same two textures and must treat them alike.
  // test the whole source, not a truncated capture.
  const post = SH.FS_POST;
  check('full post shader samples the scene unflipped',
    /texture2D\(\s*uScene\s*,\s*uv\s*[,)]/.test(post), 'scene sample is flipped or rewritten');
  check('full post shader samples the HUD flipped',
    /texture2D\(\s*uHud\s*,\s*vec2\(\s*uv\.x\s*,\s*1\.0\s*-\s*uv\.y\s*\)\s*\)/.test(post),
    'HUD must be sampled as vec2(uv.x, 1.0 - uv.y)');
}

console.log(bad ? '\n' + bad + ' orientation case(s) failed' : '\nPASS - the image is the right way up');
process.exit(bad ? 1 : 0);
