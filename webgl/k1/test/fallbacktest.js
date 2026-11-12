/* fallbacktest.js - a driver that refuses the full shader set must still get a
   running demo, not a black screen. The mock GL is told to fail compilation for
   every shader except the minimal pair, which is what a picky driver does.
   Also covers: no WebGL at all, and both shader sets failing. */
const fs = require('fs'), path = require('path'), vm = require('vm');
const { makeSandbox } = require('./mockenv.js');

const SRC = path.join(__dirname, '..', 'src');
const FILES = ['math.js', 'geo.js', 'gl.js', 'shaders.js', 'k1.js', 'actions.js', 'hud.js', 'music.js', 'main.js'];

// the load guard lives in index.html / k1.html, not in src/
const html = fs.readFileSync(path.join(__dirname, '..', 'k1.html'), 'utf8');
const guardSrc = [...html.matchAll(/<script>\n([\s\S]*?)\n[ \t]*<\/script>/g)].map((m) => m[1])[0];
if (!guardSrc.includes('K1 LOAD CHECK')) throw new Error('guard script not found in k1.html');

/* Pull the real minimal shader sources out of shaders.js so the allow-list is
   exact: anything else this driver sees, it rejects. */
const ALLOWED = (() => {
  const ctx = { window: {} };
  vm.createContext(ctx);
  vm.runInContext(fs.readFileSync(path.join(SRC, 'shaders.js'), 'utf8'), ctx, { filename: 'shaders.js' });
  const SH = ctx.window.SH;
  return new Set([SH.VS_MIN, SH.FS_MIN, SH.VS_BLIT, SH.FS_BLIT]);
})();

/* Reject every shader except the minimal pair: a picky driver. */
const minimalOnly = {
  compile: (sh, src) => {
    if (!ALLOWED.has(src)) throw new Error('rejected by test driver');
  }
};
const rejectAll = { compile: () => { throw new Error('this driver compiles nothing'); } };

// sanity: the allow-list must be exactly four shaders, and must not include any
// of the full set, or the test would pass for the wrong reason
if (ALLOWED.size !== 4) throw new Error('allow-list should hold 4 minimal shaders, got ' + ALLOWED.size);
for (const f of FILES) void f;

function run(label, opts2) {
  const opts = Object.assign({ audio: 'normal' }, opts2);
  makeSandbox(opts);
  let loadErr = null;
  const all = [['guard', guardSrc]].concat(FILES.map((f) => [f, fs.readFileSync(path.join(SRC, f), 'utf8')]));
  for (const [name, code] of all) {
    try { vm.runInContext(code, opts.ctx, { filename: name }); }
    catch (e) { loadErr = name + ': ' + e.message; break; }
  }
  if (loadErr) return { label, ok: false, why: 'LOAD ' + loadErr };

  const win = vm.runInContext('window', opts.ctx);
  /* The browser fires `load` after the last script. The guard's dependency
     check lives there, so exercise it: a working build must not report that
     its scripts did not initialise (it once looked for a global named Robot
     while the module exports K1). */
  for (const f of opts.listeners['window:load'] || []) {
    try { f({}); } catch (e) { loadErr = 'load handler: ' + e.message; }
  }
  if (loadErr) return { label, ok: false, why: loadErr };
  const booted = win.__K1_BOOTED === true;
  let frames = 0, err = null;
  if (booted) {
    for (let i = 0; i < 180; i++) {
      const q = opts.raf; opts.raf = [];
      for (const f of q) {
        try { f(Date.now() + i * 16); frames++; }
        catch (e) { err = e.message; break; }
      }
      if (err) break;
    }
  }
  const panel = vm.runInContext("document.getElementById('nogl')", opts.ctx);
  const shown = panel.style.display === 'flex' ? panel.textContent : null;
  return {
    label, ok: booted && !err && frames > 100 && opts.draws.count > 50,
    booted, frames, draws: opts.draws.count, err,
    panel: shown ? shown.split('\n')[0] : null,
    panelFull: shown,
    title: opts.els && opts.els.__title
  };
}

let bad = 0;
const check = (name, cond, detail) => {
  if (cond) console.log('  ok   ' + name);
  else { bad++; console.log('  FAIL ' + name + (detail ? '  <- ' + detail : '')); }
};

// the full set compiles (baseline, proves the failure injection is meaningful)
{
  const r = run('full', {});
  check('baseline: full shader set runs', r.ok && !r.panel, JSON.stringify(r));
}

// the driver rejects the full set -> whatever survived must still run
{
  const r = run('minimal', { glFail: minimalOnly });
  check('driver rejects full set: demo still boots', r.booted, r.err || 'did not boot');
  check('driver rejects full set: frames keep coming', r.frames > 100, r.frames + ' frames, err=' + r.err);
  check('driver rejects full set: real draw calls', r.draws > 50, r.draws + ' draws');
  check('driver rejects full set: a message is shown', !!r.panel, 'no message: user would not know why it looks flat');
  check('driver rejects full set: message is non-blocking', /DEGRADED/.test(r.panel || ''), r.panel);
  check('driver rejects full set: message names every program that was lost',
    ['post', 'ring', 'sky', 'shadow', 'floor', 'scene'].every((k) => r.panelFull.includes(k)),
    r.panelFull);
  check('driver rejects full set: the quoted log is short enough for a corner toast',
    (r.panelFull || '').split('\n').length < 30, (r.panelFull || '').split('\n').length + ' lines');
}

// nothing compiles at all -> must say so clearly, and not pretend to work.
// The blit is what dies first now: it is the only route from the offscreen
// buffer to the screen, so there is genuinely nothing left to show.
{
  const r = run('dead', { glFail: rejectAll });
  check('nothing compiles: demo reports failure', !r.booted && !!r.panel, JSON.stringify(r));
  check('nothing compiles: message names the blit as the blocker',
    /BLIT SHADER FAILED/.test(r.panel || ''), r.panel);
  check('nothing compiles: message says why that is fatal',
    /only path from the\s+offscreen buffer to the screen/.test((r.panelFull || '').replace(/\s+/g, ' ')),
    (r.panelFull || '').slice(0, 200));
  check('nothing compiles: does not claim to be running', r.frames === 0, r.frames + ' frames');
}

// the blit survives but the lit robot shader and its flat stand-in both fail:
// there is a way to the screen but no way to draw the robot, so the demo must
// stop rather than show an empty stage
{
  const r = run('norobot', { glFail: { compile: (sh, src, name) => {
    if (name === 'scene' || name === 'min') throw new Error('rejected by test driver');
  } } });
  check('no robot shader: demo reports failure', !r.booted && !!r.panel, JSON.stringify(r));
  check('no robot shader: message blames the lit shader and the flat one',
    /BOTH SHADER SETS FAILED/.test(r.panel || ''), r.panel);
  check('no robot shader: does not claim to be running', r.frames === 0, r.frames + ' frames');
}

console.log(bad ? '\n' + bad + ' fallback case(s) failed' : '\nPASS - a bad driver degrades instead of dying');
process.exit(bad ? 1 : 0);
