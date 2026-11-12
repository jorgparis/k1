/* check.js - run every harness against the real source. Exit 1 on any failure.
   Usage: node test/check.js            (add --rebuild to regenerate k1.html first) */
const { execFileSync } = require('child_process');
const path = require('path');

const here = __dirname;
if (process.argv.includes('--rebuild')) {
  console.log('### rebuilding k1.html');
  execFileSync(process.execPath, [path.join(here, 'bundle.js')], { stdio: 'inherit' });
  console.log('');
}

const STEPS = [
  ['glslcheck.js', 'GLSL program pairs'],
  ['k1test.js', 'the 17-joint rig'],
  ['smoke.js', 'render loop, both builds'],
  ['audiofail.js', 'audio cannot break the demo'],
  ['guardtest.js', 'error panel never hides a working demo'],
  ['fallbacktest.js', 'a rejecting driver degrades instead of dying'],
  ['degraderest.js', 'a rejected shader costs only its own effect'],
  ['orienttest.js', 'the image is the right way up'],
  ['shadesim.js', 'every part of the robot resolves to a lit surface'],
  ['controltest.js', 'every control works in the fallback renderer too']
];

let failed = 0;
for (const [file, what] of STEPS) {
  console.log('### ' + what + '  (' + file + ')');
  try {
    const out = execFileSync(process.execPath, [path.join(here, file)], { encoding: 'utf8' });
    process.stdout.write(out.split('\n').filter((l) => l.trim() && !/^\s*ok\s/.test(l)).map((l) => '  ' + l.trim()).join('\n') + '\n');
  } catch (e) {
    failed++;
    process.stdout.write((e.stdout || '') + (e.stderr || ''));
  }
  console.log('');
}
console.log(failed ? failed + ' harness(es) FAILED' : 'ALL HARNESSES PASS');
process.exit(failed ? 1 : 0);
