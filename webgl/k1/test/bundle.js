/* Build k1.html: one self-contained file with all CSS and JS inlined.
   Exists because a directory of separate .js files is easy to open wrongly --
   clicking src/k1.js in a file browser just shows source text. One file
   cannot be mis-navigated, and it still works from file:// with no server. */
const fs = require('fs'), path = require('path');
const DIR = require('path').resolve(__dirname, '..');
const FILES = ['math.js', 'geo.js', 'gl.js', 'shaders.js', 'k1.js', 'actions.js', 'hud.js', 'music.js', 'main.js'];

let html = fs.readFileSync(path.join(DIR, 'index.html'), 'utf8');
const css = fs.readFileSync(path.join(DIR, 'style.css'), 'utf8');

// inline the stylesheet in place of the <link>
if (!/<link rel="stylesheet" href="style\.css">/.test(html)) throw new Error('stylesheet link not found');
html = html.replace(/<link rel="stylesheet" href="style\.css">/, '<style>\n' + css + '\n</style>');

// replace the script tags with inline bodies, in the same order
const inlined = [];
for (const f of FILES) {
  const tag = new RegExp('[ \\t]*<script src="src/' + f.replace('.', '\\.') + '"></script>\\n?');
  if (!tag.test(html)) throw new Error('script tag not found for ' + f);
  const code = fs.readFileSync(path.join(DIR, 'src', f), 'utf8');
  // a literal </script inside a string would close the inline block early
  if (/<\/script/i.test(code)) throw new Error(f + ' contains a literal </script');
  inlined.push(code);
  html = html.replace(tag, '<script>\n' + code + '\n</script>\n');
}

// the CSS must not be able to close a script block either
if (/<\/script/i.test(css)) throw new Error('style.css contains a literal </script');

// verify the result: exactly one inline block per module, in order
// block 0 is the load-check script already in index.html, so modules start at 1
const blocks = [...html.matchAll(/<script>\n([\s\S]*?)\n[ \t]*<\/script>/g)].map((m) => m[1]);
const OFF = blocks.length - FILES.length;
if (OFF < 0) throw new Error('found fewer inline blocks than modules');
for (let i = 0; i < FILES.length; i++) {
  if (blocks[i + OFF] !== inlined[i]) throw new Error('inline block ' + (i + OFF) + ' does not match ' + FILES[i]);
}
// and no external references left behind
const leftover = html.match(/(?:src|href)="(?!data:)[^"]*\.(?:js|css)"/g);
if (leftover) throw new Error('external references survived the bundle: ' + leftover.join(', '));

fs.writeFileSync(path.join(DIR, 'k1.html'), html);
console.log('wrote k1.html  ' + (Buffer.byteLength(html) / 1024).toFixed(1) + ' kB  (' +
  FILES.length + ' modules + css inlined, 0 external refs)');
