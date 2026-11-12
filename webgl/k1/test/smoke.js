/* Headless smoke test: run the demo against the mock browser in mockenv.js.
   Covers load-time errors, runtime exceptions in the render loop, non-finite
   values reaching GL, and uniform/attribute names main.js uses that the
   shaders never declare. Runs both the multi-file build and the single-file
   k1.html bundle, because those are two different ways the user can open
   the demo and they must behave identically. */
const fs = require('fs'), path = require('path'), vm = require('vm');
const { makeSandbox } = require('./mockenv.js');

const ROOT = require('path').resolve(__dirname, '..');
const SRC = path.join(ROOT, 'src');
const FILES = ['math.js', 'geo.js', 'gl.js', 'shaders.js', 'k1.js', 'actions.js', 'hud.js', 'music.js', 'main.js'];

function loadBundle() {
  const html = fs.readFileSync(path.join(ROOT, 'k1.html'), 'utf8');
  const all = [...html.matchAll(/<script>\n([\s\S]*?)\n[ \t]*<\/script>/g)].map((m) => m[1]);
  const bodies = all.slice(-FILES.length);          // block 0 is the load-check script
  if (bodies.length !== FILES.length) throw new Error('expected ' + FILES.length + ' inline modules, found ' + bodies.length);
  return bodies;
}

function run(label, sources) {
  const problems = [], notes = [];
  const opts = { audio: 'normal' };
  const sandbox = makeSandbox(opts);

  for (let i = 0; i < FILES.length; i++) {
    const name = sources === 'files' ? FILES[i] : 'k1.html#' + FILES[i];
    try { vm.runInContext(sources === 'files'
      ? fs.readFileSync(path.join(SRC, FILES[i]), 'utf8') : sources[i], opts.ctx, { filename: name }); }
    catch (e) {
      problems.push('LOAD ' + name + ': ' + e.message + '\n    ' + (e.stack || '').split('\n')[1]);
      break;
    }
  }

  if (!problems.length) {
    notes.push('loaded ' + FILES.length + ' modules');
    // click past the intro gate the way a user does
    for (const fn of opts.listeners['intro:click'] || []) {
      try { fn({}); } catch (e) { problems.push('INTRO CLICK THREW: ' + e.message); }
    }
    let frames = 0;
    for (let i = 0; i < 240 && !problems.length; i++) {
      const q = opts.raf; opts.raf = [];
      for (const f of q) {
        try { f(Date.now() + i * 16.7); frames++; }
        catch (e) { problems.push('RUNTIME frame ' + frames + ': ' + e.message + '\n    ' + (e.stack || '').split('\n')[1]); break; }
      }
    }
    if (!problems.length) notes.push('ran ' + frames + ' frames after the intro click');
    const panel = opts.els['nogl'];
    // the panel is a corner toast now, never a full-screen overlay: what
    // matters is that the demo is actually rendering
    if (opts.draws.count < 100) problems.push('only ' + opts.draws.count + ' draw calls in 240 frames - nothing is being rendered');
    else notes.push(opts.draws.count + ' draw calls, ' + Math.round(opts.draws.tris) + ' tris/frame');
    if (panel && panel.style.display === 'flex') problems.push('panel shown on a healthy run: ' + panel.textContent.slice(0, 200));
  }

  const bad = [...new Set(opts.nan)];
  if (bad.length) problems.push('non-finite values passed to GL: ' + bad.slice(0, 6).join(', '));
  else notes.push('no NaN/Inf reached any GL call');

  // cross-check the uniform/attribute names main.js asks for against the GLSL
  const declared = new Set();
  const shCode = fs.readFileSync(path.join(SRC, 'shaders.js'), 'utf8');
  for (const n of require('./mockenv.js').parseDecls(shCode).uniforms) declared.add(n);
  for (const n of require('./mockenv.js').parseDecls(shCode).attribs) declared.add(n);
  const mainCode = fs.readFileSync(path.join(SRC, 'main.js'), 'utf8');
  const usedU = new Set([...mainCode.matchAll(/\.u\.(\w+)/g)].map((m) => m[1]));
  const usedA = new Set([...mainCode.matchAll(/\.a\.(\w+)/g)].map((m) => m[1]));
  const missU = [...usedU].filter((n) => !declared.has(n));
  const missA = [...usedA].filter((n) => !declared.has(n));
  if (missU.length) problems.push('uniforms used by main.js but not declared in any shader: ' + missU.join(', '));
  else notes.push('all ' + usedU.size + ' uniform names used by main.js exist in the GLSL');
  if (missA.length) problems.push('attributes used by main.js but not declared: ' + missA.join(', '));
  else notes.push('all ' + usedA.size + ' attribute names exist in the GLSL');

  console.log('--- ' + label + ' ---');
  notes.forEach((n) => console.log('  ok   ' + n));
  problems.forEach((p) => console.log('  FAIL ' + p));
  return problems.length;
}

let bad = 0;
bad += run('multi-file build (index.html + src/)', 'files');
bad += run('single-file build (k1.html)', loadBundle());
console.log(bad ? '\n' + bad + ' problem(s)' : '\nPASS');
process.exit(bad ? 1 : 0);
