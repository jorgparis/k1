/* guardtest.js - behaviour of the on-page error panel.
   The panel must never hide a working demo. These are the cases that matter:
     - a sub-resource 404 (favicon, image) must NOT raise an alarm
     - a stylesheet 404 must NOT raise an alarm
     - a failed <script> must be reported, and as fatal
     - a real JS error after the demo is running shows a corner toast only
     - the toast is dismissible by click and by keypress
   The old guard reported resource errors as "JAVASCRIPT ERROR undefined" and,
   once shown, the panel covered the canvas with no way to clear it. */
const fs = require('fs'), path = require('path'), vm = require('vm');
const { makeSandbox } = require('./mockenv.js');

const ROOT = path.join(__dirname, '..');
const SRC = path.join(ROOT, 'src');
const FILES = ['math.js', 'geo.js', 'gl.js', 'shaders.js', 'k1.js', 'actions.js', 'hud.js', 'music.js', 'main.js'];

const html = fs.readFileSync(path.join(ROOT, 'k1.html'), 'utf8');
const guardSrc = [...html.matchAll(/<script>\n([\s\S]*?)\n[ \t]*<\/script>/g)].map((m) => m[1])[0];
if (!guardSrc.includes('K1 LOAD CHECK')) throw new Error('guard script not found in k1.html');

function boot() {
  const opts = { audio: 'normal' };
  makeSandbox(opts);
  vm.runInContext(guardSrc, opts.ctx, { filename: 'guard' });
  for (const f of FILES) vm.runInContext(fs.readFileSync(path.join(SRC, f), 'utf8'), opts.ctx, { filename: f });
  const win = vm.runInContext('window', opts.ctx);
  // the guard only materialises the panel when it has something to say, which
  // is itself the behaviour under test; create it now so it can be inspected
  const panel = vm.runInContext("document.getElementById('nogl')", opts.ctx);
  return {
    opts, win, panel,
    visible: () => panel && panel.style.display === 'flex',
    isSticky: () => panel && panel.className === 'sticky',
    fire: (key, ev) => { for (const fn of opts.listeners[key] || []) fn(ev); },
    frames: (n) => {
      let c = 0;
      for (let i = 0; i < n; i++) { const q = opts.raf; opts.raf = []; for (const f of q) { f(Date.now() + i * 16); c++; } }
      return c;
    }
  };
}

let bad = 0;
const check = (name, cond, detail) => {
  if (cond) console.log('  ok   ' + name);
  else { bad++; console.log('  FAIL ' + name + (detail ? '  <- ' + detail : '')); }
};

// 1. a failed favicon / image must be ignored
{
  const t = boot();
  t.frames(30);
  t.fire('window:error', { target: { tagName: 'IMG' }, message: '' });
  t.fire('window:error', { target: { tagName: 'LINK' } });
  t.fire('window:error', { target: { tagName: 'IMG', src: '/favicon.ico' } });
  check('sub-resource 404 (favicon/img/css) is ignored', !t.visible(),
    'panel showed: ' + (t.panel.textContent || '').slice(0, 60));
}

// 2. a failed script is reported, and sticks until dismissed
{
  const t = boot();
  t.frames(30);
  t.fire('window:error', { target: { tagName: 'SCRIPT', src: 'src/k1.js' } });
  check('failed <script> is reported', t.visible());
  check('failed <script> is sticky', t.isSticky());
  check('failed <script> names the file', /k1\.js/.test(t.panel.textContent));
}

// 3. a real JS error after boot is a corner toast, and the demo survives
{
  const t = boot();
  const before = t.frames(60);
  t.fire('window:error', { target: t.win, message: 'boom', filename: 'main.js', lineno: 42 });
  check('JS error after boot is shown', t.visible());
  check('JS error after boot is not sticky', !t.isSticky());
  check('toast leaves the canvas clickable', t.panel.style.pointerEvents !== 'auto');
  const after = t.frames(60);
  check('demo keeps rendering after a toast', after === before, before + ' then ' + after);
  check('toast includes the message', /boom/.test(t.panel.textContent));
}

// 4. the toast can be cleared
{
  const t = boot();
  t.frames(30);
  t.fire('window:error', { target: t.win, message: 'boom', filename: 'main.js', lineno: 1 });
  check('toast visible before dismissal', t.visible());
  t.fire('document:click', {});
  check('click dismisses the toast', !t.visible());
  t.fire('window:error', { target: t.win, message: 'again', filename: 'main.js', lineno: 2 });
  t.fire('window:keydown', { key: 'x' });
  check('keypress dismisses the toast', !t.visible());
}

// 5. the panel can never take over the screen
{
  const css = fs.readFileSync(path.join(ROOT, 'style.css'), 'utf8');
  const rule = (css.match(/#nogl\s*\{[^}]*\}/) || [''])[0];
  check('#nogl has no full-screen inset rule', !/inset\s*:/.test(rule), rule.slice(0, 80));
  check('#nogl is pointer-transparent', /pointer-events\s*:\s*none/.test(rule));
  check('no #nogl.fatal full-screen variant exists', !/#nogl\.fatal/.test(css));
}

// 6. a healthy demo never shows the panel at all
{
  const t = boot();
  t.frames(30);
  for (const fn of t.opts.listeners['intro:click'] || []) fn({});
  t.frames(240);
  check('healthy demo never shows the panel', !t.visible(),
    'panel showed: ' + (t.panel.textContent || '').slice(0, 80));
}

console.log(bad ? '\n' + bad + ' guard case(s) failed' : '\nPASS - the panel never hides a working demo');
process.exit(bad ? 1 : 0);
