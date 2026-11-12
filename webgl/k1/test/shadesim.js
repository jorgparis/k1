/* shadesim.js - resolve each vertex the way a driver would and run the real
   FS_SCENE maths on it.

   Why this exists: the mock GL had no buffers at all, so bindBuffer,
   bufferData and vertexAttribPointer were no-ops and every buffer was an
   indistinguishable object. Nothing in the suite could tell the difference
   between "aColor is bound to a real buffer" and "aColor is never bound", so a
   robot body rendering pure black was invisible to all of them. The symptom
   only appears on screen, on a real driver.

   This harness boots the real demo, snapshots the GL state at each draw call,
   and for every vertex of the robot computes:

     VS_SCENE:  vW = uModel * aPos,  vN = uNrm * aNormal,  vC = aColor
     FS_SCENE:  the shading formula, term by term

   It then reports, per part, whether the result is black, and which term
   collapsed. A body that comes out black is a body the user sees as a
   silhouette. */
const fs = require('fs'), path = require('path'), vm = require('vm');
const { makeSandbox } = require('./mockenv.js');

const SRC = path.join(__dirname, '..', 'src');
const FILES = ['math.js', 'geo.js', 'gl.js', 'shaders.js', 'k1.js', 'actions.js', 'hud.js', 'music.js', 'main.js'];
const html = fs.readFileSync(path.join(__dirname, '..', 'k1.html'), 'utf8');
const guardSrc = [...html.matchAll(/<script>\n([\s\S]*?)\n[ \t]*<\/script>/g)].map((m) => m[1])[0];

/* ------------------------------------------------------------------ boot */
const opts = { audio: 'normal' };
makeSandbox(opts);
const all = [['guard', guardSrc]].concat(FILES.map((f) => [f, fs.readFileSync(path.join(SRC, f), 'utf8')]));
for (const [name, code] of all) vm.runInContext(code, opts.ctx, { filename: name });
for (const fn of opts.listeners['intro:click'] || []) fn({});
for (let i = 0; i < 20; i++) { const q = opts.raf; opts.raf = []; for (const f of q) f(Date.now() + i * 16); }

/* -------------------------------------------------- snapshot each draw call */
const { current, uniformVal } = opts.glState;
const origDraw = opts.gl.drawElements;
const snapshots = [];
opts.gl.drawElements = function (mode, count, type, off) {
  const u = uniformVal.get(current.p) || new Map();
  const attrs = new Map();
  for (const [loc, a] of current.attrs) attrs.set(loc.__a, { buf: a.buf, size: a.size, off: a.off });
  snapshots.push({ mode, count, prog: current.p && current.p.__prog, attrs, u: Object.fromEntries(u) });
  return origDraw.call(this, mode, count, type, off);
};

for (let i = 0; i < 2; i++) { const q = opts.raf; opts.raf = []; for (const f of q) f(Date.now() + i * 16); }

/* ------------------------------------------------------------- the maths */
const sub3 = (a, b) => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
const dot3 = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const cross3 = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
const norm3 = (a) => { const l = Math.hypot(a[0], a[1], a[2]); return l ? [a[0] / l, a[1] / l, a[2] / l] : [NaN, NaN, NaN]; };

function fetch(att, idx, size) {
  if (!att || !att.buf || !att.buf.data) return new Array(size).fill(0);
  const d = att.buf.data;
  const o = att.off / 4 + idx * size;
  const out = [];
  for (let k = 0; k < size; k++) out.push(d[o + k] === undefined ? 0 : d[o + k]);
  return out;
}
function xform4(m, v) {
  return [0, 1, 2, 3].map((r) => m[r] * v[0] + m[4 + r] * v[1] + m[8 + r] * v[2] + m[12 + r] * v[3]);
}
function xform3m3(m, v) {
  return [0, 1, 2].map((r) => m[r] * v[0] + m[3 + r] * v[1] + m[6 + r] * v[2]);
}

/* Only the robot draws in the scene program. The shadow pass shares VS_SCENE
   and so resolves the same three attributes, which is exactly why the program
   name is used to tell them apart. */
const robot = snapshots.filter((s) => s.prog === 'scene');
if (!robot.length) {
  console.log('FAIL  no draw in the scene program; saw programs: '
    + [...new Set(snapshots.map((s) => s.prog))].join(','));
  process.exit(1);
}

let bad = 0;
const check = (name, cond, detail) => {
  if (cond) console.log('  ok   ' + name);
  else { bad++; console.log('  FAIL ' + name + (detail ? '  <- ' + detail : '')); }
};

console.log('robot draws resolved: ' + robot.length);

/* Per part, run every vertex through VS+FS and look at the outcome. */
const perDraw = robot.map((s, di) => {
  const n = s.count;                       // drawElements count
  const pos = s.attrs.get('aPos'), nrm = s.attrs.get('aNormal'), col = s.attrs.get('aColor');
  const model = s.u.uModel, nm = s.u.uNrm;
  const cam = s.u.uCam, light = s.u.uLight;
  const fogCol = s.u.uFogCol || [0, 0, 0], fog = s.u.uFog || 0;
  const mode = s.u.uMode || 0;
  let sum = 0, m = 0, nans = 0, zeroN = 0, minC = 1e9, maxC = -1e9;
  let terms = { key: 0, fill: 0, sky: 0, spec: 0, rim: 0 };
  const limit = Math.min(n, 400);
  for (let i = 0; i < limit; i++) {
    const P = fetch(pos, i, 3), N = fetch(nrm, i, 3), C = fetch(col, i, 4);
    const w = xform4(model, [P[0], P[1], P[2], 1]);
    const vW = [w[0], w[1], w[2]];
    const nv = xform3m3(nm, N);
    if (Math.hypot(nv[0], nv[1], nv[2]) < 1e-9) zeroN++;
    // matches the guarded normalize in FS_SCENE / FS_MIN
    const nl = Math.hypot(nv[0], nv[1], nv[2]);
    const n = nl > 0 ? [nv[0] / nl, nv[1] / nl, nv[2] / nl] : [0, 1, 0];
    if (!n.every(Number.isFinite)) { nans++; continue; }
    const v = norm3(sub3(cam, vW));
    const l = norm3(light);
    const key = Math.max(dot3(n, l), 0);
    const fill = Math.max(dot3(n, norm3([-0.55, 0.30, -0.45])), 0);
    const sky = 0.5 + 0.5 * n[1];
    const h = norm3([l[0] + v[0], l[1] + v[1], l[2] + v[2]]);
    const spec = Math.pow(Math.max(dot3(n, h), 0), 22) * 0.22;
    const rim = Math.pow(1 - Math.max(dot3(n, v), 0), 3);
    terms.key += key; terms.fill += fill; terms.sky += sky; terms.spec += spec; terms.rim += rim;
    let c = [C[0] * (0.20 + 0.70 * key + 0.20 * fill),
             C[1] * (0.20 + 0.70 * key + 0.20 * fill),
             C[2] * (0.20 + 0.70 * key + 0.20 * fill)];
    const k2 = 0.20 + 0.70 * key + 0.20 * fill;
    c = [C[0] * k2, C[1] * k2, C[2] * k2];
    c = [c[0] + C[0] * sky * 0.22, c[1] + C[1] * sky * 0.22, c[2] + C[2] * sky * 0.22];
    c = [c[0] + spec, c[1] + spec, c[2] + spec];
    c = [c[0] + 0.20 * rim * 0.30, c[1] + 0.40 * rim * 0.30, c[2] + 0.70 * rim * 0.30];
    const a = Math.min(1, Math.max(0, C[3] || 0));
    c = [c[0] + (C[0] * 1.45 - c[0]) * a, c[1] + (C[1] * 1.45 - c[1]) * a, c[2] + (C[2] * 1.45 - c[2]) * a];
    if (mode > 1.5) c = [0.15 + 0.85 * key].map(() => 0.15 + 0.85 * key);
    const dist = Math.hypot(cam[0] - vW[0], cam[1] - vW[1], cam[2] - vW[2]);
    const f = Math.min(1, Math.max(0, (dist - 5) / 45)) * fog;
    c = [c[0] + (fogCol[0] - c[0]) * f, c[1] + (fogCol[1] - c[1]) * f, c[2] + (fogCol[2] - c[2]) * f];
    if (!c.every(Number.isFinite)) { nans++; continue; }
    const lum = 0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2];
    sum += lum; m++;
    if (lum < minC) minC = lum;
    if (lum > maxC) maxC = lum;
  }
  const t = (x) => x / Math.max(1, limit);
  return {
    di, verts: m, nans, zeroN, minC, maxC, meanC: sum / Math.max(1, m),
    terms: { key: t(terms.key), fill: t(terms.fill), sky: t(terms.sky), spec: t(terms.spec), rim: t(terms.rim) },
    modelOK: Array.from(model || []).every(Number.isFinite),
    nmOK: Array.from(nm || []).every(Number.isFinite),
    nmLen: (nm || []).length, modelLen: (model || []).length
  };
});

console.log('\n  #  verts   mean   min    max    zeroN  NaN   key   fill  sky   spec  rim');
perDraw.forEach((d, i) => {
  console.log('  ' + String(i).padStart(2) + '  ' + String(d.verts).padStart(5)
    + '  ' + d.meanC.toFixed(3) + ' ' + d.minC.toFixed(3) + ' ' + d.maxC.toFixed(3)
    + '  ' + String(d.zeroN).padStart(5) + '  ' + String(d.nans).padStart(4)
    + '  ' + d.terms.key.toFixed(2) + '  ' + d.terms.fill.toFixed(2) + '  ' + d.terms.sky.toFixed(2)
    + '  ' + d.terms.spec.toFixed(2) + '  ' + d.terms.rim.toFixed(2));
});

/* ------------------------------------------------------------- assertions */
/* The data contract the shaders rely on. Checked on the geometry itself rather
   than through the shading maths, because the shipped shaders now guard their
   own normalize() - so a zero normal would be caught by that guard rather than
   showing up as black here, and the thing worth asserting is that geo.js never
   produces one in the first place. */
{
  const ctx = { window: {}, console };
  ctx.global = ctx;
  vm.createContext(ctx);
  for (const f of ['math.js', 'geo.js', 'k1.js'])
    vm.runInContext(fs.readFileSync(path.join(SRC, f), 'utf8'), ctx, { filename: f });
  const parts = ctx.window.K1.buildParts();
  let zero = 0, offUnit = 0, tris = 0;
  for (const p of parts) {
    const n = p.geo.nrm;
    for (let i = 0; i < n.length; i += 3) {
      const l = Math.hypot(n[i], n[i + 1], n[i + 2]);
      if (l < 1e-9) zero++;
      else if (Math.abs(l - 1) > 1e-3) offUnit++;
    }
    tris += p.geo.idx.length / 3;
    if (n.length !== p.geo.pos.length) { bad++; console.log('  FAIL ' + p.name + ': normal count != position count'); }
  }
  check('every part has a non-zero normal on every vertex (a zero normal is NaN in normalize(), i.e. black)',
    zero === 0, zero + ' zero-length normals across ' + parts.length + ' parts');
  check('every normal is unit length (flat shading needs per-face normals, not an average)',
    offUnit === 0, offUnit + ' normals were not unit length');
  console.log('  (' + parts.length + ' parts, ' + tris + ' triangles)');
}

check('every draw uploaded a 4x4 model matrix', perDraw.every((d) => d.modelLen === 16 && d.modelOK),
  JSON.stringify(perDraw.map((d) => d.modelLen)));
check('every draw uploaded a 3x3 normal matrix', perDraw.every((d) => d.nmLen === 9 && d.nmOK),
  JSON.stringify(perDraw.map((d) => d.nmLen)));
check('no vertex has a zero-length normal (normalize(0) is undefined in GLSL and renders black)',
  perDraw.every((d) => d.zeroN === 0), 'zero normals: ' + perDraw.map((d) => d.zeroN).join(','));
check('no vertex produces NaN', perDraw.every((d) => d.nans === 0), 'NaNs: ' + perDraw.map((d) => d.nans).join(','));
check('every part is lit: the key light term is non-zero',
  perDraw.every((d) => d.terms.key > 0.01), 'key: ' + perDraw.map((d) => d.terms.key.toFixed(3)).join(','));
check('no part renders black', perDraw.every((d) => d.meanC > 0.02),
  'mean luminance: ' + perDraw.map((d) => d.meanC.toFixed(3)).join(','));
check('no part is a flat silhouette (there is a visible range)',
  perDraw.every((d) => d.maxC - d.minC > 0.02),
  'range: ' + perDraw.map((d) => (d.maxC - d.minC).toFixed(3)).join(','));

console.log(bad ? '\n' + bad + ' shading case(s) failed'
  : '\nPASS - every part of the robot resolves to a lit, non-black surface');
process.exit(bad ? 1 : 0);
