/* degraderest.js - a driver that rejects some shaders must lose only those.
   The bug this exists to catch: the renderer was all-or-nothing. One rejected
   program threw out the other five, so a driver that disliked only the post
   pass lost the lighting, the floor grid, the shadows and the sky as well -
   which showed up as "it works but the details are missing".
   Each case injects a driver that rejects a chosen subset and asserts the
   surviving programs are actually used: a feature that compiled must still be
   drawn. The draw calls are read off the real GL stream. */
const fs = require('fs'), path = require('path'), vm = require('vm');
const { makeSandbox } = require('./mockenv.js');

const SRC = path.join(__dirname, '..', 'src');
const FILES = ['math.js', 'geo.js', 'gl.js', 'shaders.js', 'k1.js', 'actions.js', 'hud.js', 'music.js', 'main.js'];
const html = fs.readFileSync(path.join(__dirname, '..', 'k1.html'), 'utf8');
const guardSrc = [...html.matchAll(/<script>\n([\s\S]*?)\n[ \t]*<\/script>/g)].map((m) => m[1])[0];

const SHCTX = { window: {} };
vm.createContext(SHCTX);
vm.runInContext(fs.readFileSync(path.join(SRC, 'shaders.js'), 'utf8'), SHCTX, { filename: 'shaders.js' });
const SH = SHCTX.window.SH;

/* every program the renderer can build, by the name it is built under. This
   table is what makes the injection exact, and it fails loudly if main.js ever
   renames one. */
const PROGRAMS = ['post', 'ring', 'sky', 'shadow', 'floor', 'scene', 'min', 'blit'];
const mainSrc = fs.readFileSync(path.join(SRC, 'main.js'), 'utf8');
for (const k of PROGRAMS) {
  if (!mainSrc.includes("'" + k + "'")) throw new Error('main.js does not build a program named ' + k);
}
void SH;

let bad = 0;
const check = (name, cond, detail) => {
  if (cond) console.log('  ok   ' + name);
  else { bad++; console.log('  FAIL ' + name + (detail ? '  <- ' + detail : '')); }
};

/* Boot with a driver that rejects `reject` (an array of program names).
   Rejection is by program name, not by source: scene and shadow legitimately
   share VS_SCENE, and post and blit use the same fullscreen triangle, so a
   source-based rule would take out innocent programs and the test would be
   measuring the wrong thing. */
function bootPrograms(reject) {
  const banned = new Set(reject);
  const glFail = {
    compile: (sh, src, name) => {
      if (banned.has(name)) throw new Error('rejected by test driver: ' + name);
    }
  };
  const opts = { audio: 'normal', trace: true, glFail };
  makeSandbox(opts);
  const all = [['guard', guardSrc]].concat(FILES.map((f) => [f, fs.readFileSync(path.join(SRC, f), 'utf8')]));
  for (const [name, code] of all) vm.runInContext(code, opts.ctx, { filename: name });
  for (const fn of opts.listeners['intro:click'] || []) fn({});
  for (let i = 0; i < 20; i++) { const q = opts.raf; opts.raf = []; for (const f of q) f(Date.now() + i * 16); }
  opts.glCalls.mark();
  for (let i = 0; i < 1; i++) { const q = opts.raf; opts.raf = []; for (const f of q) f(Date.now() + i * 16); }
  const seen = new Set();
  const g = opts.glCalls.log;
  for (let i = g.mark; i < g.length; i++) if (g[i].f === 'useProgram') seen.add(g[i].p);
  const win = vm.runInContext('window', opts.ctx);
  const panel = win.document.getElementById('nogl');
  return {
    booted: win.__K1_BOOTED === true,
    perFrame: seen.size,
    draws: opts.draws.count,
    panel: panel.style.display === 'flex' ? panel.textContent : ''
  };
}

/* ---- baseline: everything compiles, everything is used ---- */
{
  const r = bootPrograms([]);
  check('baseline: demo boots with nothing rejected', r.booted && r.draws > 20, JSON.stringify(r));
  check('baseline: all six programs used in one frame', r.perFrame === 6, 'programs used: ' + r.perFrame);
  check('baseline: no error panel', r.panel === '', r.panel.slice(0, 80));
}

/* ---- one rejected program must cost only that program ----
   A full frame uses six programs. Rejecting `post` swaps it for the blit, so
   the count stays six; every other rejection drops the count to five. The
   count is the point: it proves the surviving programs are actually bound and
   drawn, not merely compiled. */
const CASES = [
  { reject: ['post'], keep: 6, note: 'post -> the blit takes over the screen copy' },
  { reject: ['sky'], keep: 5, note: 'sky -> clears to the horizon colour instead' },
  { reject: ['ring'], keep: 5, note: 'ring -> no impact rings' },
  { reject: ['shadow'], keep: 5, note: 'shadow -> no contact shadow' },
  { reject: ['floor'], keep: 5, note: 'floor -> no floor grid' }
];
for (const c of CASES) {
  const r = bootPrograms(c.reject);
  check(c.reject[0] + ': demo still boots', r.booted, JSON.stringify(r));
  check(c.reject[0] + ': everything else is still drawn (' + c.note + ')',
    r.perFrame === c.keep, 'programs used: ' + r.perFrame + ' (expected ' + c.keep + ')');
  check(c.reject[0] + ': still rendering, not blank', r.draws > 20, 'draws ' + r.draws);
  check(c.reject[0] + ': names the rejected program in the message',
    new RegExp(c.reject[0].toUpperCase()).test(r.panel), r.panel.slice(0, 160));
}

/* ---- the lit robot shader is the expensive one to lose. The sky, the floor
   grid, the shadow and the rings are separate programs and must survive it:
   short-circuiting the whole scene instead is what produced a demo that worked
   but looked like it had no detail in it. ---- */
{
  const r = bootPrograms(['scene']);
  check('scene rejected: demo still boots', r.booted, JSON.stringify(r));
  check('scene rejected: post, ring, sky, shadow and floor all still drawn',
    r.perFrame === 6, 'programs used: ' + r.perFrame + ' (expected 6)');
  check('scene rejected: still rendering', r.draws > 20, 'draws ' + r.draws);
  check('scene rejected: the message says the robot went flat',
    /DEGRADED/.test(r.panel) && /flat colour/i.test(r.panel), r.panel.slice(0, 200));
  check('scene rejected: the message does not blame the surviving effects',
    !/post|sky|ring/.test(r.panel.split('To get the full look back')[0]),
    r.panel.slice(0, 200));
}

/* ---- several rejected: still boots, and says exactly what went ---- */
{
  const r = bootPrograms(['scene', 'post', 'sky']);
  check('three rejected: demo still boots', r.booted, JSON.stringify(r));
  check('three rejected: ring, shadow, floor, min and blit are the five left',
    r.perFrame === 5, 'programs used: ' + r.perFrame);
  check('three rejected: message lists every lost program',
    ['scene', 'post', 'sky'].every((k) => r.panel.includes(k)), r.panel.slice(0, 200));
  /* Only the first two logs are quoted, to keep the corner toast small, so the
     remaining names have to be legible in the summary line on their own. */
  check('three rejected: the summary still names all three',
    /post, sky, scene|post.*sky.*scene/i.test(r.panel), r.panel.slice(0, 200));
}

/* ---- losing the blit is fatal, because nothing can reach the screen ---- */
{
  const r = bootPrograms(['blit', 'post']);
  check('no blit and no post: the demo says so instead of showing nothing',
    !r.booted && /BLIT SHADER FAILED/.test(r.panel), JSON.stringify({ booted: r.booted, panel: r.panel.slice(0, 80) }));
}

/* ---- robot and floor both gone: the blit still carries the image, on a flat
   plate drawn by the minimal shader ---- */
{
  const r = bootPrograms(['scene', 'floor']);
  check('robot and floor both gone: the blit still carries the image', r.booted, JSON.stringify(r));
  check('robot and floor both gone: min, sky, shadow, ring and post remain',
    r.perFrame === 5, 'programs used: ' + r.perFrame);
}

console.log(bad ? '\n' + bad + ' degradation case(s) failed' : '\nPASS - a rejected shader costs only its own effect');
process.exit(bad ? 1 : 0);
