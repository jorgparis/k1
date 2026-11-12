/* GLSL ES 1.00 pair validator for shaders.js.
   The only rule that actually breaks a hand-written program is a fragment
   shader reading a varying the vertex shader does not write. Everything else
   checked here (a VS-only uniform, a VS-only varying) is legal GLSL.
   Preprocessor lines are stripped and #define macros are expanded first, so
   macro bodies are checked like ordinary code. */
const fs = require('fs');
const path = require('path');
global.window = global;
eval(fs.readFileSync(require('path').join(__dirname, '..', 'src', 'shaders.js'), 'utf8'));
const SH = global.SH;
const PAIRS = [
  ['scene', SH.VS_SCENE, SH.FS_SCENE], ['shadow', SH.VS_SCENE, SH.FS_SHADOW],
  ['floor', SH.VS_FLOOR, SH.FS_FLOOR], ['sky', SH.VS_SKY, SH.FS_SKY],
  ['ring', SH.VS_RING, SH.FS_RING], ['post', SH.VS_POST, SH.FS_POST],
  // the fallback set must be valid too, or a bad driver gets a black screen
  ['min', SH.VS_MIN, SH.FS_MIN], ['blit', SH.VS_BLIT, SH.FS_BLIT]
];
const stripC = (s) => s.replace(/\/\*[\s\S]*?\*\//g, ' ').replace(/\/\/[^\n]*/g, ' ');

function preprocess(src) {
  let s = stripC(src);
  const defs = new Map();
  for (const m of s.matchAll(/^\s*#define\s+(\w+)(\([^)]*\))?\s+([^\n]*)$/gm)) defs.set(m[1], m[3]);
  s = s.replace(/^\s*#\w+[^\n]*$/gm, ' ');              // drop directive lines
  // expand macros iteratively (handles bayer8 -> bayer2)
  for (let i = 0; i < 6; i++) {
    let before = s;
    for (const [k, v] of defs) s = s.replace(new RegExp('\\b' + k + '\\s*\\(([^()]*)\\)', 'g'), '(' + v + ')');
    if (s === before) break;
  }
  for (const [k, v] of defs) s = s.replace(new RegExp('\\b' + k + '\\b', 'g'), '(' + v + ')');
  return s;
}
const declRe = (kw) => new RegExp('^\\s*' + kw + '\\s+(?:lowp|mediump|highp\\s+)?(\\w+)\\s+([^;]+);', 'gm');
function decls(src, kw) {
  const out = new Set();
  for (const m of src.matchAll(declRe(kw)))
    for (const n of m[2].split(',')) {
      const t = n.trim().match(/(\w+)\s*(\[\s*\w+\s*\])?$/);
      if (t) out.add(t[1]);
    }
  return out;
}
const BUILTIN = new Set(('gl_Position gl_FragColor gl_FragCoord gl_PointSize gl_PointCoord gl_FrontFacing ' +
  'gl_FragData gl_DepthRange gl_VertexID gl_InstanceID texture2D dFdx dFdy fwidth mod atan sign clamp mix step ' +
  'smoothstep length normalize cross dot reflect refract pow exp log sqrt abs floor ceil fract min max sin cos tan ' +
  'radians degrees inversesqrt mat2 mat3 mat4 vec2 vec3 vec4 float int bool if else for while return discard const ' +
  'struct precision highp mediump lowp void true false bvec2 bvec3 bvec4 ivec2 ivec3 ivec4 uint uvec2 uvec3 ' +
  'uvec4 round trunc sinh cosh tanh isnan isinf not lessThan greaterThan any all notEqual equal matrixCompMult ' +
  'outerProduct transpose determinant inverse attribute uniform varying in out inout layout sampler2D').split(' '));

let bad = 0;
for (const [name, vsRaw, fsRaw] of PAIRS) {
  const vs = preprocess(vsRaw), fsrc = preprocess(fsRaw);
  const probs = [];
  const vW = decls(vs, 'varying'), fR = decls(fsrc, 'varying');
  for (const v of fR) if (!vW.has(v)) probs.push('FATAL: FS reads varying "' + v + '" the VS never writes');
  if (!/\bprecision\s+(lowp|mediump|highp)\s+float/.test(fsrc)) probs.push('FATAL: FS has no float precision qualifier');
  if (!/\bvoid\s+main\s*\(/.test(vs) || !/\bvoid\s+main\s*\(/.test(fsrc)) probs.push('FATAL: missing main()');
  // brace balance
  for (const [lbl, code] of [['VS', vs], ['FS', fsrc]]) {
    let d = 0; for (const ch of code) { if (ch === '{') d++; else if (ch === '}') d--; }
    if (d !== 0) probs.push('FATAL: ' + lbl + ' brace imbalance (' + d + ')');
    const p = (code.match(/\(/g) || []).length - (code.match(/\)/g) || []).length;
    if (p !== 0) probs.push('FATAL: ' + lbl + ' paren imbalance (' + p + ')');
  }
  /* Obvious type mismatches that a browser driver rejects with a wall of
     "dimension mismatch" errors. This is not a full type checker, but it
     catches the shape that actually broke the sky: a float declared from a
     vector constructor or from a vector-returning builtin applied to one.
     (`float wcell = floor(vec2(...))` cost a whole program.) */
  const scalarFromVector = /\bfloat\s+(\w+)\s*=\s*(?:vec[234]\s*\(|(?:floor|fract|normalize|abs|sin|cos|tan|atan|exp|log|sqrt|sign|clamp|mix|min|max|step|smoothstep|pow|reflect|refract)\s*\(\s*vec[234]\s*\()/g;
  const vectorFromScalar = /\bvec[234]\s+(\w+)\s*=\s*(?:length|dot|distance|cross)\s*\(/g;
  for (const [lbl, code] of [['VS', vs], ['FS', fsrc]]) {
    for (const m of code.matchAll(scalarFromVector))
      probs.push('FATAL: ' + lbl + ' float "' + m[1] + '" is assigned a vector expression');
    for (const m of code.matchAll(vectorFromScalar))
      probs.push('FATAL: ' + lbl + ' vector "' + m[1] + '" is assigned a scalar expression');
  }
  // undeclared identifiers
  for (const [lbl, code] of [['VS', vs], ['FS', fsrc]]) {
    const known = new Set([...decls(code, 'varying'), ...decls(code, 'uniform'), ...decls(code, 'attribute'), ...BUILTIN]);
    for (const m of code.matchAll(/\b(?:float|vec2|vec3|vec4|int|bool|mat2|mat3|mat4)\s+(\w+)/g)) known.add(m[1]);
    for (const m of code.matchAll(/([A-Za-z_]\w*)\s*(?:\[[^\]]*\])?\s*[-+*\/&|]?=/g)) known.add(m[1]);
    for (const m of code.matchAll(/\b(\w+)\s*\(/g)) known.add(m[1]);
    for (const m of code.matchAll(/(?<![.\w])([A-Za-z_]\w*)/g)) {
      const id = m[1];
      if (known.has(id) || BUILTIN.has(id) || /^(gl_|GL_)/.test(id)) continue;
      if (/^(v|f|b|i|w|x|y|z|r|g|bl|al|st)$/.test(id)) continue;
      if (/^[a-z_]/.test(id) && id.length <= 2) continue;
      probs.push('FATAL: ' + lbl + ' undeclared identifier "' + id + '"');
    }
  }
  if (probs.length) { bad++; console.log('[' + name + ']'); probs.forEach((p) => console.log('   - ' + p)); }
  else console.log('[' + name + '] ok');
}
console.log(bad ? '\n' + bad + ' program(s) with findings' : '\nall ' + PAIRS.length + ' pairs consistent: no undeclared ids, no bad varyings, braces/precision ok');
process.exit(bad ? 1 : 0);
