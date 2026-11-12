/* mockenv.js - shared headless browser stand-in for the k1 harnesses.
   Provides a WebGL1 context proxy, a 2D canvas context proxy, a DOM, and a
   window. The WebGL mock parses real uniform/attribute names out of the GLSL
   the shaders are compiled from, so uniform tables are genuine and a mismatch
   between main.js and the shaders shows up as a missing name. */
const vm = require('vm');

function parseDecls(src) {
  const uniforms = [], attribs = [];
  const clean = src.replace(/\/\*[\s\S]*?\*\//g, ' ').replace(/\/\/[^\n]*/g, ' ');
  for (const m of clean.matchAll(/^\s*uniform\s+(?:lowp|mediump|highp\s+)?\w+\s+([^;]+);/gm))
    for (const nm of m[1].split(',')) {
      const n = nm.trim().match(/(\w+)\s*(\[\s*\w+\s*\])?$/);
      if (n) uniforms.push(n[1]);
    }
  for (const m of clean.matchAll(/^\s*attribute\s+(?:lowp|mediump|highp\s+)?\w+\s+([^;]+);/gm))
    for (const nm of m[1].split(',')) {
      const n = nm.trim().match(/(\w+)$/);
      if (n) attribs.push(n[1]);
    }
  return { uniforms, attribs };
}

const ACTIVE_UNIFORMS = 0x8B86, ACTIVE_ATTRIBUTES = 0x8B89, COMPLETE = 0x8CD5;

function makeGL(bad, opts) {
  let enumN = 0x1000;
  const enums = new Map();
  const shaderSrc = new WeakMap();
  const shaderOK = new WeakMap();
  /* Real GL state, so a harness can resolve a vertex the way the driver would:
     which buffer each attribute reads, and what value each uniform holds for
     the program currently in use. */
  let bufN = 0;
  const bound = new Map();
  const current = { p: null, enabled: new Set(), attrs: new Map() };
  const uniformVal = new Map();  // program -> Map(uniformName -> value)
  const setU = (p, loc, v) => {
    if (!p || !loc) return;
    if (!uniformVal.has(p)) uniformVal.set(p, new Map());
    uniformVal.get(p).set(loc.__u, v);
  };
  current.arrayTarget = () => gl.ARRAY_BUFFER;
  /* Read the label off `target`, not `impl`: the proxy has no set handler, so
     gl.__k1Program = name lands on target, and impl is only a copy of it. */
  const progName = () => (target.__k1Program || '');
  const progDecls = new Map();
  const attached = new Map();
  const nan = [];
  const draws = { count: 0, tris: 0 };
  /* Opt-in structured call log. Some questions are only answerable from what
     actually reached GL - "does pressing W change the draw mode, or just the
     HUD text" - and counting draws cannot see that. Recording every call in
     every harness would be wasteful, so it is off unless asked for. */
  const trace = opts.trace ? [] : null;
  const mark = () => { if (trace) trace.mark = trace.length; };
  const log = (f, extra) => { if (trace) trace.push(Object.assign({ f }, extra)); };

  const record = (fn, args) => {
    for (let i = 1; i < args.length; i++) {
      const a = args[i];
      if (typeof a === 'number' && !Number.isFinite(a)) { nan.push(fn + ' arg' + i + '=' + a); return; }
      if (ArrayBuffer.isView(a)) {
        for (let k = 0; k < a.length; k++)
          if (!Number.isFinite(a[k])) { nan.push(fn + '[' + k + ']=' + a[k]); return; }
      }
    }
  };

  /* The GL enums are synthesized by the proxy, so ELEMENT_ARRAY_BUFFER has no
     value at this point. Resolve it on first use, from the finished proxy -
     reading it off the raw target would give undefined. */
  let eboTarget = null;
  const eboBuf = () => {
    if (eboTarget === null) eboTarget = gl.ELEMENT_ARRAY_BUFFER;
    return eboTarget;
  };

  const impl = {
    createShader: (t) => ({ t }),
    shaderSource: (sh, s) => shaderSrc.set(sh, s),
    compileShader: (sh) => {
      // `bad.compile` is the injection point: throw to make this shader fail.
      // Real drivers reject some shaders and accept others, so the verdict has
      // to be per shader, not global.
      let ok = true;
      if (bad && bad.compile) {
        /* the third argument is the program gl.js is building, so a harness can
           reject one program even when two of them share a source string */
        try { bad.compile(sh, shaderSrc.get(sh) || '', progName()); } catch (e) { ok = false; }
      }
      shaderOK.set(sh, ok);
    },
    getShaderParameter: (sh) => shaderOK.get(sh) !== false,
    getShaderInfoLog: (sh) => (shaderOK.get(sh) === false ? 'ERROR: 0:1: rejected by the test driver' : ''),
    /* tagged with the name gl.js is building, so a harness can tell the scene
       program from the shadow program even though they share a vertex shader */
    createProgram: () => ({ __prog: target.__k1Program || '?' }),
    attachShader: (p, sh) => { const a = attached.get(p) || []; a.push(sh); attached.set(p, a); },
    linkProgram: (p) => {
      const src = (attached.get(p) || []).map((s) => shaderSrc.get(s) || '').join('\n');
      progDecls.set(p, parseDecls(src));
    },
    getProgramParameter: (p, pname) => {
      const d = progDecls.get(p);
      if (!d) return 0;
      /* Compare against the proxy's own enum values. The GL constants here are
         synthesized, so hardcoding 0x8B86 silently never matches and this used
         to fall through to `return 1` - which made every uniform and attribute
         table in every harness a one entry stub. */
      if (pname === gl.ACTIVE_UNIFORMS) return d.uniforms.length;
      if (pname === gl.ACTIVE_ATTRIBUTES) return d.attribs.length;
      return 1;
    },
    getProgramInfoLog: () => '',
    getActiveUniform: (p, i) => ({ name: (progDecls.get(p) || { uniforms: [] }).uniforms[i] || '?' }),
    getActiveAttrib: (p, i) => ({ name: (progDecls.get(p) || { attribs: [] }).attribs[i] || '?' }),
    getUniformLocation: (p, nm) => ({ __u: nm }),
    getAttribLocation: (p, nm) => ({ __a: nm }),
    getExtension: () => ({}),
    getParameter: () => 0,
    checkFramebufferStatus: () => COMPLETE,
    drawElements: (mode, count) => {
      draws.count++; draws.tris += count / 3;
      log('drawElements', { mode, count });
    },
    drawArrays: (mode, first, count) => { draws.count++; draws.tris += count / 3; log('drawArrays', { mode, count }); },
    bindBuffer: (t, buf) => { bound.set(t, buf); if (t === eboBuf()) log('bindEBO', { buf }); },
    useProgram: (p) => { current.p = p; log('useProgram', { p }); },
    /* the location object carries its own name, so a uniform upload can be
       logged by name - "did uColor actually change" is otherwise unanswerable */
    uniform1f: (l, v) => { setU(current.p, l, v); log('u1f', { n: l && l.__u, v }); },
    uniform1i: (l, v) => { setU(current.p, l, v); log('u1i', { n: l && l.__u, v }); },
    uniform3f: (l, x, y, z) => { setU(current.p, l, [x, y, z]); log('u3f', { n: l && l.__u, v: [x, y, z] }); },
    uniform3fv: (l, v) => { setU(current.p, l, Array.from(v || [])); log('u3v', { n: l && l.__u, v: Array.from(v || []) }); },

    /* ---- buffer and attribute state ----------------------------------------
       Without these, every buffer was an indistinguishable no-op and every
       vertexAttribPointer did nothing, so "the colour attribute is not bound
       and the body renders black" was completely invisible to every harness.
       A buffer is now a real object holding its data, and the attribute
       pointers are recorded against the program in use, which is enough to
       resolve a vertex the way the driver would. */
    createBuffer: () => ({ __buf: ++bufN, data: null }),
    bufferData: (t, data) => { const b = bound.get(t); if (b) b.data = data; },
    bufferSubData: (t, off, data) => {
      const b = bound.get(t);
      if (b && b.data && data) b.data.set(data, off);
    },
    enableVertexAttribArray: (loc) => { current.enabled.add(loc); },
    disableVertexAttribArray: (loc) => { current.enabled.delete(loc); },
    vertexAttribPointer: (loc, size, type, norm, stride, off) => {
      current.attrs.set(loc, { buf: bound.get(current.arrayTarget()), size, off: off || 0, norm: !!norm });
    },
    uniformMatrix3fv: (l, tr, v) => { setU(current.p, l, Array.from(v || [])); log('um3', { n: l && l.__u, v: Array.from(v || []) }); },
    uniformMatrix4fv: (l, tr, v) => { setU(current.p, l, Array.from(v || [])); log('um4', { n: l && l.__u, v: Array.from(v || []) }); },
    viewport: () => {}
  };
  for (const k of Object.keys(impl)) {
    if (typeof impl[k] === 'function' && k !== 'createShader' && k !== 'createProgram') {
      const f = impl[k];
      impl[k] = function (...a) { record(k, a); return f.apply(this, a); };
    }
  }
  const target = Object.assign(Object.create(null), impl, {
    canvas: { width: 1280, height: 800 },
    drawingBufferWidth: 1280, drawingBufferHeight: 800
  });
  const gl = new Proxy(target, {
    get(t, prop) {
      if (prop in t) return t[prop];
      if (typeof prop === 'string' && /^[A-Z0-9_]+$/.test(prop)) {
        if (!enums.has(prop)) enums.set(prop, enumN++);
        return enums.get(prop);
      }
      if (typeof prop === 'string') return () => {};
      return undefined;
    },
    has: () => true
  });
  return { gl, nan, draws, trace, mark, state: { bound, current, uniformVal } };
}

function make2D() {
  const sink = {};
  return new Proxy(sink, {
    get(t, p) {
      if (p === 'canvas') return { width: 320, height: 200 };
      if (p === 'measureText') return () => ({ width: 10 });
      if (p === 'createLinearGradient' || p === 'createRadialGradient')
        return () => ({ addColorStop: () => {} });
      if (p === 'createImageData' || p === 'getImageData')
        return (x, y, w, h) => ({ data: new Uint8ClampedArray(w * h * 4), width: w, height: h });
      if (p === 'fillStyle' || p === 'strokeStyle' || p === 'font' || p === 'globalAlpha' ||
        p === 'textAlign' || p === 'textBaseline' || p === 'lineWidth' || p === 'globalCompositeOperation')
        return t[p];
      if (typeof p === 'symbol') return undefined;
      return () => {};
    },
    set(t, p, v) { t[p] = v; return true; }
  });
}

/* Build a sandbox wired up like a browser. `audio` picks the AudioContext
   behaviour: 'normal' | 'absent' | 'throws' | 'schedulethrows'. */
function makeSandbox(opts) {
  opts = opts || {};
  const { gl, nan, draws, trace, mark, state } = makeGL(opts.glFail, opts);
  opts.glState = state;
  const ctx2d = make2D();
  const listeners = {};
  const on = (obj, key) => { (listeners[key] = listeners[key] || []).push(obj); };

  /* A DOM node with just enough of the real thing for the demo's own code:
     the 17-servo panel is built at runtime with createElement/appendChild and
     driven through `value`/`dataset`, and a test needs to read those back. */
  const mkEl = (id) => {
    const classes = new Set();
    const el = {
      id, width: 1280, height: 800, textContent: '', innerHTML: '',
      value: '', type: '', min: '', max: '', step: '', checked: false,
      dataset: {}, children: [], parentNode: null,
      style: { display: '' },
      classList: {
        add: (c) => classes.add(c),
        remove: (c) => classes.delete(c),
        toggle: (c, on) => { if (on === undefined ? !classes.has(c) : on) classes.add(c); else classes.delete(c); },
        contains: (c) => classes.has(c)
      },
      addEventListener: (t, f) => { el._on = el._on || {}; (el._on[t] = el._on[t] || []).push(f); on(f, id + ':' + t); },
      removeEventListener() {},
      appendChild: (c) => { el.children.push(c); c.parentNode = el; return c; },
      removeChild: (c) => { const i = el.children.indexOf(c); if (i >= 0) el.children.splice(i, 1); return c; },
      setAttribute: (k, v) => { el[k] = v; },
      getAttribute: (k) => el[k],
      select() {}, focus() {}, blur() {},
      querySelectorAll: () => [],
      getContext: (k) => (k === 'webgl' || k === 'experimental-webgl') ? gl : ctx2d,
      setPointerCapture() {},
      getBoundingClientRect: () => ({ left: 0, top: 0, width: 1280, height: 800 })
    };
    return el;
  };

  const els = {};
  const quiet = { log() {}, warn(...a) { opts.warnings.push('warn: ' + a[0]); }, error(...a) { opts.warnings.push('error: ' + a[0]); } };
  opts.warnings = opts.warnings || [];

  const acNode = () => ({
    gain: { value: 1, setValueAtTime() {}, linearRampToValueAtTime() {}, exponentialRampToValueAtTime() {}, cancelScheduledValues() {}, setTargetAtTime() {} },
    frequency: { value: 440, setValueAtTime() {}, exponentialRampToValueAtTime() {}, linearRampToValueAtTime() {}, cancelScheduledValues() {} },
    Q: { value: 1 }, detune: { value: 0 }, type: '', playbackRate: { value: 1 },
    threshold: { value: 0 }, knee: { value: 0 }, ratio: { value: 1 }, attack: { value: 0 }, release: { value: 0 },
    buffer: null, loop: false,
    connect() {}, disconnect() {}, setPeriodicWave() {},
    start() { if (opts.audio === 'schedulethrows') throw new Error('stale audio node'); },
    stop() {}
  });

  const sandbox = {
    console: quiet,
    performance: { now: () => Date.now() },
    requestAnimationFrame: (f) => { opts.raf.push(f); return opts.raf.length; },
    cancelAnimationFrame() {},
    setTimeout: () => 0, clearTimeout() {}, setInterval: () => 0, clearInterval() {},
    Math, Date, JSON, Object, Array, Float32Array, Uint8Array, Uint16Array, Uint32Array,
    Int32Array, Uint8ClampedArray, Float64Array, Map, Set, WeakMap, Error, String, Number, Boolean,
    isNaN, parseFloat, parseInt, Infinity, NaN, undefined,
    innerWidth: 1280, innerHeight: 800, devicePixelRatio: 1,
    location: { href: 'file:///index.html', protocol: 'file:' },
    navigator: { userAgent: 'node' },
    addEventListener: (t, f) => on(f, 'window:' + t),
    removeEventListener() {},
    fetch: () => Promise.resolve({ ok: true }),
    requestFullscreen: () => {},
    fullscreenElement: null
  };
  sandbox.window = sandbox;
  sandbox.globalThis = sandbox;
  sandbox.ImageData = function (d, w, h) { this.data = d; this.width = w; this.height = h; };
  if (opts.audio === 'normal' || opts.audio === 'schedulethrows') {
    sandbox.AudioContext = function () {
      return {
        currentTime: 0, sampleRate: 44100, state: 'running', destination: {},
        createGain: acNode, createOscillator: acNode, createBuffer: () => ({ getChannelData: () => new Float32Array(44100) }),
        createBufferSource: acNode, createBiquadFilter: acNode, createDynamicsCompressor: acNode,
        createPeriodicWave: () => ({}), resume() {}, close() {}
      };
    };
  } else if (opts.audio === 'throws') {
    const e = new Error('autoplay blocked'); e.name = 'NotAllowedError';
    sandbox.AudioContext = function () { throw e; };
  }
  // 'absent' => AudioContext deliberately undefined

  sandbox.document = {
    getElementById: (id) => (els[id] || (els[id] = mkEl(id))),
    createElement: () => mkEl('dyn'),
    addEventListener: (t, f) => on(f, 'document:' + t),
    // the demo writes its error message into the intro panel's "click to start"
    // line, so it needs to resolve that selector
    querySelector: (sel) => (els['sel:' + sel] || (els['sel:' + sel] = mkEl('sel:' + sel))),
    // the panel asks for its preset buttons; a static-HTML node list is not
    // modelled, so this is empty and the presets are exercised via K1 directly
    querySelectorAll: () => [],
    execCommand: () => true,
    body: mkEl('body'), title: '', documentElement: mkEl('html')
  };

  opts.raf = [];
  opts.listeners = listeners;
  opts.els = els;
  opts.nan = nan;
  opts.draws = draws;
  /* opt-in GL call log, see makeGL */
  opts.glCalls = { log: trace, mark };
  opts.gl = gl;
  /* the enums are synthesized, so a test comparing draw modes must read the
     real values from here rather than hardcoding 0x0004 and friends */
  opts.glEnums = new Proxy({}, { get: (t, p) => gl[p] });
  opts.ctx = vm.createContext(sandbox);
  return sandbox;
}

module.exports = { makeSandbox, makeGL, make2D, parseDecls };
