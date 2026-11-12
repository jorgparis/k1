/* controltest.js - in the fallback renderer, every control must change something.
   The bug this exists to catch: the fallback drew flat colour and ignored
   sim.wire / sim.hueOn / sim.mode, so W, C and N registered the keypress and
   then changed nothing on screen. From the user's side that is identical to a
   demo with dead keys, and it survived every other harness because they all
   exercise the full render path.
   The rule enforced here: for the fallback path, each control must change the
   draw mode, the element buffer, or the colour uniform. All three are read off
   the actual GL call stream, not off a flag. */
const fs = require('fs'), path = require('path'), vm = require('vm');
const { makeSandbox } = require('./mockenv.js');

const SRC = path.join(__dirname, '..', 'src');
const FILES = ['math.js', 'geo.js', 'gl.js', 'shaders.js', 'k1.js', 'actions.js', 'hud.js', 'music.js', 'main.js'];
const html = fs.readFileSync(path.join(__dirname, '..', 'k1.html'), 'utf8');
const guardSrc = [...html.matchAll(/<script>\n([\s\S]*?)\n[ \t]*<\/script>/g)].map((m) => m[1])[0];

/* the same picky driver as fallbacktest: only the minimal set compiles */
const ALLOWED = (() => {
  const ctx = { window: {} };
  vm.createContext(ctx);
  vm.runInContext(fs.readFileSync(path.join(SRC, 'shaders.js'), 'utf8'), ctx, { filename: 'shaders.js' });
  const SH = ctx.window.SH;
  return new Set([SH.VS_MIN, SH.FS_MIN, SH.VS_BLIT, SH.FS_BLIT]);
})();
const minimalOnly = { compile: (sh, src) => { if (!ALLOWED.has(src)) throw new Error('rejected'); } };
const SH = (() => { const c = { window: {} }; vm.createContext(c); vm.runInContext(fs.readFileSync(path.join(SRC, 'shaders.js'), 'utf8'), c); return c.window.SH; })();

let bad = 0;
const check = (name, cond, detail) => {
  if (cond) console.log('  ok   ' + name);
  else { bad++; console.log('  FAIL ' + name + (detail ? '  <- ' + detail : '')); }
};

/* Boot the fallback, press keys, and record what the GL stream actually did. */
function probe(keys, glFail) {
  const opts = { audio: 'normal', trace: true, glFail };
  makeSandbox(opts);
  const all = [['guard', guardSrc]].concat(FILES.map((f) => [f, fs.readFileSync(path.join(SRC, f), 'utf8')]));
  for (const [name, code] of all) vm.runInContext(code, opts.ctx, { filename: name });

  for (const fn of opts.listeners['intro:click'] || []) fn({});
  const run = (n) => {
    for (let i = 0; i < n; i++) {
      const q = opts.raf; opts.raf = [];
      for (const f of q) f(Date.now() + i * 16);
    }
  };
  run(20);                                   // settle, so sim.t is not the variable

  const snap = (E) => {
    const g = opts.glCalls.log;
    /* only frames drawn since the keypress: sim.hue animates continuously, so
       a snapshot over the whole log would never settle */
    const from = g.mark || 0;
    const s = { tri: 0, line: 0, ebo: 0, colors: [], uModes: [] };
    for (let i = from; i < g.length; i++) {
      const c = g[i];
      if (c.f === 'drawElements') {
        if (c.mode === E.TRIANGLES) s.tri++; else if (c.mode === E.LINES) s.line++;
      } else if (c.f === 'bindEBO') s.ebo++;
      else if (c.f === 'u3f' && c.n === 'uColor') s.colors.push(c.v.map((x) => Math.round(x * 1e3) / 1e3));
      else if (c.f === 'u1f' && c.n === 'uMode') s.uModes.push(c.v);
    }
    /* The floor is drawn first, once, and deliberately stays solid and unlit in
       every mode - that matches the full render path, where the floor has its
       own program. So drop it before judging what the controls did to the robot. */
    s.parts = s.uModes.slice(1);
    s.modes = [...new Set(s.parts)];
    s.colors = [...new Set(s.colors.map((c) => c.join(',')))].sort();
    return s;
  };

  const trace = [];
  for (const k of keys) {
    for (const fn of opts.listeners['window:keydown'] || []) fn({ key: k, preventDefault() {} });
    run(2);
    opts.glCalls.mark();                     // only look at frames after the key
    run(1);
    trace.push(snap(opts.glEnums));
  }
  if (!keys.length) { opts.glCalls.mark(); run(1); trace.push(snap(opts.glEnums)); }
  return { trace, win: vm.runInContext('window', opts.ctx) };
}

console.log('  (fallback renderer: full shader set rejected)');

/* sanity: we really are on the fallback path, or the rest proves nothing */
{
  const p = probe([], minimalOnly);
  const panel = p.win.document.getElementById('nogl');
  check('probe really is running the fallback', /DEGRADED/.test(panel.textContent),
    'panel: ' + panel.textContent.slice(0, 60));
}

/* baseline: solid triangles for the floor and every part */
{
  const s = probe([], minimalOnly).trace[0];
  check('baseline draws the floor and all parts as solid triangles', s.tri > 1 && s.line === 0,
    'tri=' + s.tri + ' line=' + s.line);
  check('baseline uploads a shading mode for the whole robot', s.parts.length === 1 && s.modes.join() === '0',
    'robot uploads=' + s.parts.length + ' modes=' + s.modes.join(','));
}

/* W: the robot must switch to lines and bind the wireframe index buffer.
   The floor staying solid is correct, so it is excluded above. */
{
  const s = probe(['w'], minimalOnly).trace[0];
  check('W switches the robot to LINES', s.line > 1 && s.tri === 1,
    'tri=' + s.tri + ' line=' + s.line + ' (expected 1 floor triangle)');
  check('W binds the wireframe element buffer', s.ebo > 0, 'ebo binds=' + s.ebo);
}

/* C: must change the colours actually uploaded, and keep changing them */
{
  const base = probe([], minimalOnly).trace[0].colors;
  const run = probe(new Array(9).fill('c'), minimalOnly);
  const at = (i) => JSON.stringify(run.trace[i].colors);
  check('C changes the uploaded part colours', at(0) !== JSON.stringify(base),
    'base=' + base.slice(0, 2) + ' after=' + run.trace[0].colors.slice(0, 2));
  check('C keeps animating rather than latching once', at(1) !== at(0) && at(2) !== at(1));
  check('cycled colours are still valid RGB', run.trace[4].colors.every((c) => {
    const v = c.split(',').map(Number);
    return v.length === 3 && v.every((x) => x >= -0.01 && x <= 1.01);
  }), run.trace[4].colors.slice(0, 3).join(' '));
}

/* N: must reach the shader, not just the HUD text */
{
  const m1 = probe(['n'], minimalOnly).trace[0];
  const m2 = probe(['n', 'n'], minimalOnly).trace[1];
  const m0 = probe(['n', 'n', 'n'], minimalOnly).trace[2];
  check('N puts every part in normal-shading mode', m1.modes.join() === '1',
    'part modes=' + m1.modes.join(','));
  check('N cycles on to mono light and back to solid',
    m2.modes.join() === '2' && m0.modes.join() === '0',
    'mode2=' + m2.modes.join(',') + ' mode0=' + m0.modes.join(','));
  /* Matches the intent, not one spelling: the flat shader must build its
     normal from the vN varying, and must normalise it (either as
     normalize(vN) or as an explicit divide). The explicit divide exists so a
     zero normal cannot become NaN and render the body black. */
  check('FS_MIN can shade from a real per-face normal',
    /vec3 n\s*=/.test(SH.FS_MIN) && /normalize\(vN\)|vN\s*\/\s*\w+/.test(SH.FS_MIN),
    'FS_MIN does not derive a normalised n from vN');
  check('VS_MIN supplies a world-space normal', /mat3\(uModel\)\s*\*\s*aNormal/.test(SH.VS_MIN));
}

/* wireframe and shading must compose rather than clobber each other. Mode is
   forced to 2 under wireframe, exactly as the full render path does, so the
   wire lines are shaded by the light instead of showing raw normals. */
{
  const s = probe(['w', 'n'], minimalOnly).trace[1];
  check('W still wins the draw mode while N changes shading',
    s.line > 1 && s.tri === 1 && s.modes.join() === '2',
    'tri=' + s.tri + ' line=' + s.line + ' partModes=' + s.modes.join(','));
}

/* the CPU hue rotation must agree with hue() in FS_SCENE, or C would look
   different in the fallback than in the full renderer */
{
  const c = { window: {} };
  vm.createContext(c);
  vm.runInContext(fs.readFileSync(path.join(SRC, 'math.js'), 'utf8'), c, { filename: 'math.js' });
  const M4 = c.window.M4;
  /* transcribed literally from FS_SCENE */
  const K = 0.57735;
  const glslHue = (v, a) => {
    const ca = Math.cos(a);
    const cr = [K * v[1] - K * v[2], K * v[2] - K * v[0], K * v[0] - K * v[1]];
    const kd = K * v[0] + K * v[1] + K * v[2];
    return [0, 1, 2].map((i) => v[i] * ca + cr[i] * Math.sin(a) + K * kd * (1 - ca));
  };
  const out = new Float32Array(3);
  let worst = 0, worstLen = 0;
  for (let i = 0; i < 24; i++) {
    const a = (i / 24) * Math.PI * 2;
    const col = [0.95, 0.5, 0.11];
    const mine = [M4.hueRotate(out, col, a)[0], out[1], out[2]];
    const ref = glslHue(col, a);
    worst = Math.max(worst, ...mine.map((x, j) => Math.abs(x - ref[j])));
    worstLen = Math.max(worstLen, Math.abs(
      Math.hypot(...mine) - Math.hypot(...col)));
  }
  check('the CPU hue rotation matches hue() in FS_SCENE', worst < 1e-6, 'worst delta ' + worst.toExponential(2));
  /* Not tighter: HUE_K is 0.57735, a 6 digit stand-in for 1/sqrt(3) =
     0.5773502691, so the axis is not exactly unit length and a "rotation" by
     it drifts by ~1e-6. FS_SCENE truncates identically, which is why the two
     agree exactly and this is a property of the constant, not of the method. */
  check('the hue rotation preserves colour magnitude', worstLen < 1e-5, 'worst ' + worstLen.toExponential(2));
  check('a full turn returns the original colour', (() => {
    M4.hueRotate(out, [0.2, 0.4, 0.6], Math.PI * 2);
    return Math.abs(out[0] - 0.2) < 1e-6 && Math.abs(out[1] - 0.4) < 1e-6 && Math.abs(out[2] - 0.6) < 1e-6;
  })());
}

/* the full render path must be untouched by any of this */
{
  const p = probe([], null);
  const panel = p.win.document.getElementById('nogl');
  check('full render path still boots clean', p.win.__K1_BOOTED === true);
  check('full render path shows no error panel', panel.style.display !== 'flex', panel.textContent.slice(0, 120));
  const s = p.trace[0];
  check('full render path still draws the scene', s.tri > 0, 'tri=' + s.tri);
}

console.log(bad ? '\n' + bad + ' control case(s) failed' : '\nPASS - every control does something in the fallback too');
process.exit(bad ? 1 : 0);
