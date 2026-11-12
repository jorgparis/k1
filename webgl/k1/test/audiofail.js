/* Regression test: audio must never be able to take the demo down.
   Music.start() runs inside the intro click handler. If building the
   AudioContext throws, the demo used to die with it. Run main.js once per
   broken-audio scenario, click the intro, then keep rendering. The render
   loop must survive all of them. */
const fs = require('fs'), path = require('path'), vm = require('vm');
const { makeSandbox } = require('./mockenv.js');

const SRC = require('path').join(__dirname, '..', 'src');
const FILES = ['math.js', 'geo.js', 'gl.js', 'shaders.js', 'k1.js', 'actions.js', 'hud.js', 'music.js', 'main.js'];

function run(audio) {
  const opts = { audio };
  makeSandbox(opts);
  for (const f of FILES) {
    try { vm.runInContext(fs.readFileSync(path.join(SRC, f), 'utf8'), opts.ctx, { filename: f }); }
    catch (e) { return { audio, ok: false, why: 'LOAD ' + f + ': ' + e.message }; }
  }
  for (const fn of opts.listeners['intro:click'] || []) {
    try { fn({}); } catch (e) { return { audio, ok: false, why: 'intro click threw: ' + e.message }; }
  }
  let frames = 0;
  for (let i = 0; i < 180; i++) {
    const q = opts.raf; opts.raf = [];
    for (const f of q) {
      try { f(Date.now() + i * 16.7); frames++; }
      catch (e) { return { audio, ok: false, why: 'frame ' + frames + ': ' + e.message }; }
    }
  }
  const panel = opts.els['nogl'];
  if (panel && panel.style.display === 'flex') return { audio, ok: false, why: 'error panel: ' + panel.textContent.slice(0, 80) };
  if (frames < 100) return { audio, ok: false, why: 'only ' + frames + ' frames ran' };
  return { audio, ok: true, frames, warn: opts.warnings.length };
}

let bad = 0;
for (const mode of ['normal', 'absent', 'throws', 'schedulethrows']) {
  const r = run(mode);
  const what = { normal: 'working audio', absent: 'no AudioContext at all', throws: 'AudioContext ctor throws', schedulethrows: 'nodes throw when scheduled' }[mode];
  if (r.ok) console.log('  ok   ' + mode.padEnd(15) + what.padEnd(30) + r.frames + ' frames, demo alive' + (r.warn ? ', ' + r.warn + ' warning(s) logged' : ''));
  else { bad++; console.log('  FAIL ' + mode.padEnd(15) + what.padEnd(30) + r.why); }
}
console.log(bad ? '\n' + bad + ' audio scenario(s) broke the demo' : '\nPASS - audio can never stop the render loop');
process.exit(bad ? 1 : 0);
