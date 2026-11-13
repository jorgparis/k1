/* main.js - K1: a raw WebGL retro robot walk demo */
(function () {
  'use strict';

  const M4 = window.M4, GLU = window.GLU, Geo = window.Geo, Robot = window.K1,
    SH = window.SH, HUD = window.HUD, Music = window.Music;
  const clamp = window.clamp, lerp = window.lerp, fract = window.fract, DEG = Math.PI / 180;

  const canvas = document.getElementById('gl');

  /* Diagnostics never cover the canvas. A message goes into the intro panel
     (where the user is already looking) and the document title (visible in the
     tab), plus a small dismissible toast. The demo keeps running if it can. */
  function report(msg) {
    if (window.K1_DIED) window.K1_DIED(msg);
    try {
      const p = document.querySelector('#intro .panel .go');
      if (p) { p.textContent = msg.split('\n')[0].slice(0, 120); p.classList.add('err'); }
    } catch (e) { /* pre-DOM, nothing to do */ }
    try { document.title = 'K1 - ' + msg.split('\n')[0].slice(0, 60); } catch (e) {}
    console.error('[k1] ' + msg);
  }

  const gl = canvas.getContext('webgl', { antialias: false, alpha: false, depth: true, stencil: false })
    || canvas.getContext('experimental-webgl');
  if (!gl) {
    // say precisely why, because "WebGL is off" has several very different causes
    const probe = document.createElement('canvas');
    const p2 = probe.getContext('webgl') || probe.getContext('experimental-webgl');
    let why;
    if (!p2) {
      why = 'NO WEBGL IN THIS BROWSER\n\n'
        + 'Chrome/Edge: settings > System > "Use graphics acceleration when available"\n'
        + 'Firefox: about:config  gfx.webgl.force-enabled = true\n'
        + 'Then fully close and reopen the browser.\n\n'
        + 'Open the console (F12) and post any red WebGL error - it names the driver.';
    } else {
      why = 'THE CANVAS COULD NOT GIVE UP A WEBGL CONTEXT\n'
        + 'A probe canvas worked, so WebGL is available. This is a canvas\n'
        + 'setup problem. Press F12 and post the console output.';
    }
    window.__K1_DEAD = true;
    report(why);
    return;
  }
  window.__K1_GL = gl.getParameter(gl.VERSION) + ' | ' + (gl.getParameter(gl.RENDERER) || '?');

  const RW = 320, RH = 200;                     // internal render resolution
  const PAL = {
    zenith: [0.035, 0.045, 0.16],
    horizon: [0.52, 0.16, 0.40],
    sun: [1.0, 0.52, 0.22],
    floor: [0.055, 0.065, 0.125],
    line: [0.28, 0.82, 1.0],
    ring: [0.45, 0.95, 1.0],
    light: [0.45, 0.80, -0.42]
  };

  /* ------------------------------------------------------------- programs */
  /* Each program is compiled on its own and a rejection is recorded rather than
     thrown away. A driver that dislikes one shader - most often the post pass,
     which is by far the busiest - should cost you that one effect, not the
     lighting, the floor grid, the sky and the shadows along with it. All-or-
     nothing here is what turned a single rejected shader into "the details are
     missing". */
  const TRIES = [
    ['post', SH.VS_POST, SH.FS_POST],
    ['ring', SH.VS_RING, SH.FS_RING],
    ['sky', SH.VS_SKY, SH.FS_SKY],
    ['shadow', SH.VS_SCENE, SH.FS_SHADOW],
    ['floor', SH.VS_FLOOR, SH.FS_FLOOR],
    ['scene', SH.VS_SCENE, SH.FS_SCENE]
  ];
  const prog = {};
  const rejected = [];
  for (const [key, vs, fs] of TRIES) {
    try {
      prog[key] = GLU.program(gl, vs, fs, key);
    } catch (e) {
      rejected.push({ key, why: String(e.message || e) });
    }
  }
  const pScene = prog.scene || null, pShadow = prog.shadow || null;
  const pFloor = prog.floor || null, pSky = prog.sky || null;
  const pRing = prog.ring || null, pPost = prog.post || null;

  /* pBlit is always needed: it is the only way onto the screen if there is no
     post pass. */
  let pMin = null, pBlit = null;
  try {
    pBlit = GLU.program(gl, SH.VS_BLIT, SH.FS_BLIT, 'blit');
  } catch (e) {
    window.__K1_DEAD = true;
    report('BLIT SHADER FAILED TO COMPILE\n\n'
      + 'Nothing can be shown without it - it is the only path from the\n'
      + 'offscreen buffer to the screen. This driver is unusable.\n\n'
      + String(e.message || e).split('\n').slice(0, 8).join('\n'));
    return;
  }

  /* Only if the lit scene shader is gone do we need the flat pair for the
     robot. Everything else can keep running alongside it. */
  const minimal = !pScene;
  if (minimal) {
    try {
      pMin = GLU.program(gl, SH.VS_MIN, SH.FS_MIN, 'min');
    } catch (e) {
      window.__K1_DEAD = true;
      report('BOTH SHADER SETS FAILED TO COMPILE\n\n'
        + 'The lit scene shader was rejected: '
        + (rejected.find((r) => r.key === 'scene') || {}).why + '\n\n'
        + 'The flat fallback shader was rejected too:\n'
        + String(e.message || e).split('\n').slice(0, 8).join('\n'));
      return;
    }
  }

  if (rejected.length) {
    const lost = rejected.map((r) => r.key);
    /* Quote at most two logs. A corner toast that carries six of them is tall
       enough to cover the demo it is describing, and when a driver rejects
       everything it is almost always one quirk behind all six - the names of
       the rest are the part worth keeping. */
    const quoted = rejected.slice(0, 2)
      .map((r) => '[' + r.key.toUpperCase() + ']\n' + r.why.split('\n').slice(0, 6).join('\n'))
      .join('\n\n');
    const tail = rejected.length > 2
      ? '\n(and ' + (rejected.length - 2) + ' more, same pattern)'
      : '';
    report('DEGRADED RENDERER\n\n'
      + 'This driver rejected ' + lost.length + ' of ' + TRIES.length
      + ' shader programs: ' + lost.join(', ') + '.\n'
      + 'Everything it did accept is still running' + (minimal ? ' except the\nlit robot shading, which fell back to flat colour' : '') + '.\n\n'
      + 'To get the full look back, quote this log:\n\n' + quoted + tail);
  }

  /* ---------------------------------------------------------------- meshes */
  const parts = K1.buildParts();
  const byName = {};
  const meshes = parts.map((p) => {
    byName[p.name] = p;
    const b = Geo.toBuffers(p.geo);
    p.mesh = GLU.makeMesh(gl, b);
    p.wire = Geo.wireIdx(b.pos, b.idx);
    p.wireBuf = gl.createBuffer();
    gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, p.wireBuf);
    gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, p.wire, gl.STATIC_DRAW);
    p.wireCount = p.wire.length;
    p.world = M4.create();
    p.flat = M4.create();
    p.nm = new Float32Array(9);
    return p.mesh;
  });
  let tris = 0;
  parts.forEach((p) => tris += p.mesh.count / 3);

  const floorMesh = GLU.makeMesh(gl, Geo.toBuffers(Geo.plane(600, [1, 1, 1, 0])));
  const quadBuf = GLU.fullscreenQuad(gl);
  const ringMesh = (function () {
    // unit quad in XZ, 2 triangles
    const pos = new Float32Array([-1, 0, -1, 1, 0, -1, 1, 0, 1, -1, 0, -1, 1, 0, 1, -1, 0, 1]);
    const life = new Float32Array(12);
    const idx = new Uint16Array([0, 1, 2, 3, 4, 5]);
    const m = { count: 6 };
    m.pos = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, m.pos);
    gl.bufferData(gl.ARRAY_BUFFER, pos, gl.STATIC_DRAW);
    m.life = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, m.life);
    gl.bufferData(gl.ARRAY_BUFFER, life, gl.DYNAMIC_DRAW);
    m.idx = gl.createBuffer(); gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, m.idx);
    gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, idx, gl.STATIC_DRAW);
    return m;
  })();
  const ringLifeData = new Float32Array(12);

  /* -------------------------------------------------------------- targets */
  let target = GLU.createTarget(gl, RW, RH);
  const hudTex = GLU.createCanvasTexture(gl, HUD.W, HUD.H);

  /* --------------------------------------------------------------- camera */
  const cam = {
    yaw: -0.78, pitch: 0.18, dist: 2.05,
    tYaw: -0.78, tPitch: 0.18, tDist: 2.05,
    target: [0, 0.5, 0], auto: true, autoT: 0
  };
  const CAM_PRESETS = [
    { yaw: -0.78, pitch: 0.18, dist: 2.05 },   // three-quarter front
    { yaw: 1.55, pitch: 0.08, dist: 1.95 },    // profile: best for pitch joints
    { yaw: -1.45, pitch: 0.60, dist: 2.6 },    // high, for the hip roll
    { yaw: 0.15, pitch: 0.02, dist: 1.35 }     // close on the head
  ];

  /* ------------------------------------------------------- demo state */
  const D = K1.D;
  const JOINTS = K1.JOINTS;

  /* Three poses, not one:
       target - what the sliders and presets ask for
       joints - what is being drawn   (eases toward target)
       (the real servo value is `target`: that is what would be sent)
     Separating them is the difference between a slider that snaps the model
     and one that looks like a servo travelling. */
  const st = {
    target: K1.neutralPose(),
    joints: K1.neutralPose(),
    sel: 0,
    yaw: 0,              // model heading; the floor grid and lighting use it
    nudge: 0,
    scene: 'NEUTRAL',
    idle: true
  };

  /* The presets are part of the rig, not the renderer, so they live next to
     the joint table and a harness can check them without a browser. */
  const PRESETS = K1.PRESETS;

  /* One real stunt at a time, decoded from the card's own .sh file. Unlike a
     preset (17 static numbers) this is a clock: `act.t` runs the action, and
     `K1.actionPose` samples the keyframes. It writes target *and* joints, so
     the model follows the file's own frames instead of easing through them -
     the easing would round off exactly the sharp beats the file encodes. */
  const act = { key: null, name: null, data: null, t: 0, playing: false, panelT: 0 };

  function actions() { return window.K1ACTIONS || {}; }
  function actionList() { return window.K1ACTION_LIST || Object.keys(actions()); }

  function playAction(key) {
    const a = actions()[key];
    if (!a || !a.frames || !a.frames.length) return false;
    act.key = key;
    act.name = a.name;
    act.data = a;
    act.t = 0;
    act.playing = true;
    st.scene = 'ACTION: ' + a.name.toUpperCase();
    /* An action is a motion, not an idle pose, so stop the slow camera orbit
       that would otherwise read as the robot turning. A seated take is also
       best read from the side: from the front its forward leg fold projects
       onto the screen as a sideways spread. Only do this while the camera is
       still wandering; a view the user has chosen is left alone. */
    if (cam.auto) {
      cam.auto = false;
      if (a.contact === 'seat') {
        const p = CAM_PRESETS[1];              // profile: best for pitch joints
        cam.tYaw = p.yaw; cam.tPitch = p.pitch; cam.tDist = p.dist;
      } else {
        cam.tYaw = cam.yaw; cam.tPitch = cam.pitch; cam.tDist = cam.dist;
      }
    }
    const pick = document.getElementById('movePick');
    if (pick && pick.value !== key) pick.value = key;
    syncMoveInfo();
    return true;
  }

  /* Handing the robot back to the sliders: the pose stays where the action
     stopped, because that is where the servos would be. */
  function stopAction() {
    act.playing = false;
    act.data = null;
    act.key = null;
    act.name = null;
  }

  function applyPreset(key) {
    const p = PRESETS[key];
    if (!p) return;
    stopAction();
    st.target = K1.neutralPose();
    for (const name of Object.keys(p.raw)) {
      const j = K1.byJoint[name];
      st.target[name] = K1.clampRaw(name, j.neutral + p.raw[name]);
    }
    st.scene = p.name;
    syncPanel();
  }

  const sim = {
    t: 0, phase: 0, ground: 0, paused: false, frames: 0, fps: 60,
    wire: false, mode: 0, hue: 0, hueOn: false, hudOn: true,
    levels: 26, dither: 1, scan: 0.16, mask: 0.12, aberr: 0.5,
    rings: [], showKeys: true, keyTimer: 8
  };
  for (let i = 0; i < 8; i++) sim.rings.push({ on: false, age: 0, x: 0, z: 0, seed: 0 });

  /* ------------------------------------------------------------- matrices */
  const proj = M4.create(), view = M4.create(), vp = M4.create(), invVP = M4.create();
  const tmpM = M4.create(), rootM = M4.create();

  /* ---------------------------------------------------------------- input */
  const keys = {};
  function selectJoint(i) {
    cam.auto = false;
    st.sel = (i + JOINTS.length) % JOINTS.length;
    st.scene = JOINTS[st.sel].name.toUpperCase();
    syncPanel();
  }
  function nudge(raw) {
    cam.auto = false;
    stopAction();
    const j = JOINTS[st.sel];
    st.target[j.name] = K1.clampRaw(j.name, st.target[j.name] + raw);
    st.scene = j.name.toUpperCase();
    syncPanel();
  }
  window.addEventListener('keydown', (e) => {
    if (e.repeat) return;
    const k = e.key.toLowerCase();
    keys[k] = true;
    if (k === ' ') { sim.paused = !sim.paused; e.preventDefault(); }
    if (k === 'r') { st.yaw = 0; sim.phase = 0; sim.ground = 0; applyPreset('neutral'); }
    if (k === 'm') { Music.toggle(); updateHint(); }
    if (k === 'w') sim.wire = !sim.wire;
    if (k === 'n') sim.mode = (sim.mode + 1) % 3;
    if (k === 'c') sim.hueOn = !sim.hueOn;
    if (k === 'h') sim.hudOn = !sim.hudOn;
    if (k === 'k') sim.showKeys = !sim.showKeys;
    if (k === 'f') {
      if (!document.fullscreenElement) document.documentElement.requestFullscreen();
      else document.exitFullscreen();
    }
    /* the joint walker: arrow to pick, up/down to move it, 0 to park it.
       This is the whole point of the demo - every servo reachable by keyboard. */
    if (k === 'arrowleft') { selectJoint(st.sel - 1); e.preventDefault(); }
    if (k === 'arrowright') { selectJoint(st.sel + 1); e.preventDefault(); }
    if (k === 'arrowup') { nudge(e.shiftKey ? 1 : 5); e.preventDefault(); }
    if (k === 'arrowdown') { nudge(e.shiftKey ? -1 : -5); e.preventDefault(); }
    if (k === '0') { stopAction(); const j = JOINTS[st.sel]; st.target[j.name] = j.neutral; syncPanel(); }
    if (k === 'p') { playAction('pushups'); }
    if (k >= '1' && k <= '4') {
      const p = CAM_PRESETS[+k - 1];
      cam.tYaw = p.yaw; cam.tPitch = p.pitch; cam.tDist = p.dist; cam.auto = false;
    }
    updateHint();
  });
  window.addEventListener('keyup', (e) => { keys[e.key.toLowerCase()] = false; });

  let dragging = false, lastX = 0, lastY = 0;
  const pointer = { x: 0, y: 0, tx: 0, ty: 0, active: false };
  canvas.addEventListener('pointerdown', (e) => {
    dragging = true; lastX = e.clientX; lastY = e.clientY; cam.auto = false;
    canvas.setPointerCapture(e.pointerId);
  });
  canvas.addEventListener('pointerup', (e) => { dragging = false; });
  canvas.addEventListener('pointermove', (e) => {
    const r = canvas.getBoundingClientRect();
    pointer.tx = ((e.clientX - r.left) / r.width - 0.5) * 2;
    pointer.ty = ((e.clientY - r.top) / r.height - 0.5) * 2;
    if (dragging) {
      cam.tYaw -= (e.clientX - lastX) * 0.008;
      cam.tPitch = clamp(cam.tPitch + (e.clientY - lastY) * 0.006, -0.25, 1.2);
      lastX = e.clientX; lastY = e.clientY;
    }
  });
  canvas.addEventListener('wheel', (e) => {
    cam.tDist = clamp(cam.tDist * (1 + Math.sign(e.deltaY) * 0.09), 1.6, 12);
    cam.auto = false; e.preventDefault();
  }, { passive: false });

  /* --------------------------------------------------------------- resize */
  /* ----------------------------------------------------------------- panel */
  /* The 17 servos, one row each, built from K1.JOINTS so the panel cannot list
     a joint the model does not have. Left/right are the protocol's own names:
     a number you can type into `jointMove` is the whole point. */
  const rows = [];
  function buildPanel() {
    const host = document.getElementById('rows');
    if (!host) return;
    for (const j of JOINTS) {
      const row = document.createElement('div');
      row.className = 'row';
      row.dataset.joint = j.name;

      const lab = document.createElement('label');
      const idx = document.createElement('i');
      idx.textContent = ('0' + j.index).slice(-2);
      const nm = document.createElement('span');
      nm.textContent = j.name;
      lab.appendChild(idx);
      lab.appendChild(nm);

      const sl = document.createElement('input');
      sl.type = 'range';
      sl.min = j.min; sl.max = j.max; sl.step = 1; sl.value = j.neutral;
      sl.setAttribute('aria-label', j.name);
      sl.addEventListener('input', () => {
        cam.auto = false;
        stopAction();
        st.target[j.name] = K1.clampRaw(j.name, +sl.value);
        st.scene = j.name.toUpperCase();
        syncPanel();
      });

      const out = document.createElement('output');
      out.textContent = j.neutral;

      row.appendChild(lab); row.appendChild(sl); row.appendChild(out);
      host.appendChild(row);
      rows.push({ j, row, sl, out });
    }

    for (const b of document.querySelectorAll('#panel [data-preset]')) {
      b.addEventListener('click', () => applyPreset(b.dataset.preset));
    }
    const copy = document.getElementById('copy');
    if (copy) copy.addEventListener('click', copyPose);
    buildMoves();
    syncPanel();
  }

  function syncPanel() {
    for (const r of rows) {
      const v = Math.round(st.target[r.j.name]);
      if (+r.sl.value !== v) r.sl.value = v;
      r.out.textContent = v;
      r.row.classList.toggle('on', r.j.index === st.sel);
      r.row.classList.toggle('moved', v !== r.j.neutral);
    }
  }

  /* ---------------------------------------------------------- move browser */
  /* The phone page's whole move menu, not just Push Ups. The list and every
     clock come from src/actions.js, which test/genactions.py generates from
     the phone's data; this code only renders it, so a move cannot be invented
     here and a timing cannot drift from the one that was measured. */
  function moveClockText(a) {
    if (!a) return '—';
    if (a.available === false) {
      const d = a.duration_ms == null ? '' : (a.duration_ms / 1000).toFixed(2) + 's declared · ';
      return d + (a.reason || 'not on the card');
    }
    return (a.duration_ms / 1000).toFixed(2) + 's · ' + a.clock + ' · '
      + a.frames.length + ' keyframes';
  }

  function syncMoveInfo() {
    const pick = document.getElementById('movePick');
    const info = document.getElementById('moveInfo');
    const bar = document.getElementById('moveBar');
    if (!info) return;
    const a = (pick && actions()[pick.value]) || act.data;
    if (!a) {
      info.textContent = '—';
      if (bar) bar.style.width = '0%';
      return;
    }
    let text = moveClockText(a);
    if (a.note) text += ' · ' + a.note;
    if (act.data === a && a.duration_ms) {
      const at = act.t.toFixed(2) + ' / ' + (a.duration_ms / 1000).toFixed(2) + 's';
      text = act.playing ? at + ' · ' + a.frames.length + ' keyframes' : at + ' · held';
    }
    info.textContent = text;
    info.title = text;
    if (bar) {
      const frac = (act.data === a && a.duration_ms)
        ? Math.max(0, Math.min(1, act.t * 1000 / a.duration_ms)) : 0;
      bar.style.width = (frac * 100).toFixed(1) + '%';
    }
  }

  function buildMoves() {
    const pick = document.getElementById('movePick');
    const find = document.getElementById('moveFind');
    const play = document.getElementById('movePlay');
    const stop = document.getElementById('moveStop');
    if (!pick) return;

    /* Filtering rebuilds the options from the same generated list, so the
       browser can never show a move the data does not have. The selection is
       kept when it survives the filter; otherwise the first playable match is
       selected, which makes the Play button useful while typing. */
    function fill(preferred) {
      const needle = ((find && find.value) || '').trim().toLowerCase();
      const keep = preferred || pick.value;
      while (pick.children.length) pick.removeChild(pick.children[0]);
      const groups = {};
      const keys = [];
      for (const key of actionList()) {
        const a = actions()[key];
        if (!a) continue;
        if (needle && a.name.toLowerCase().indexOf(needle) < 0
            && key.indexOf(needle) < 0) continue;
        let group = groups[a.folder];
        if (!group) {
          group = document.createElement('optgroup');
          group.label = a.folder === 'Card' ? 'CARD SCRIPTS'
            : a.folder.toUpperCase();
          groups[a.folder] = group;
          pick.appendChild(group);
        }
        const opt = document.createElement('option');
        opt.value = key;
        opt.textContent = a.name
          + (a.available === false ? '  (not on card)'
            : (a.clock === 'estimated' ? '  ~' : ''));
        if (a.available === false) opt.disabled = true;
        group.appendChild(opt);
        keys.push(key);
      }
      const count = document.getElementById('moveCount');
      if (count) count.textContent = keys.length + '/' + actionList().length;
      if (keys.indexOf(keep) >= 0) pick.value = keep;
      else {
        const first = keys.find((k) => actions()[k].available !== false) || keys[0];
        pick.value = first || '';
      }
      syncMoveInfo();
    }

    if (find) find.addEventListener('input', fill);
    pick.addEventListener('change', syncMoveInfo);
    if (play) play.addEventListener('click', () => {
      const key = pick.value;
      if (!key) return;
      if (!playAction(key)) {
        document.getElementById('hint').textContent = 'NO ACTION DATA: ' + key;
      } else {
        document.getElementById('hint').textContent = 'PLAYING '
          + act.name.toUpperCase() + '  (' + (act.data.duration_ms / 1000).toFixed(2)
          + 's, ' + act.data.clock + ')';
      }
      syncMoveInfo();
    });
    if (stop) stop.addEventListener('click', () => { stopAction(); syncMoveInfo(); });

    /* Push Ups stays the default, because P and the old button mean that; a
       deep-linked action wins over it, since that is what the page was asked
       to show. The selection is applied after fill(), because setting a
       <select>'s value before it has matching options does nothing. */
    const initial = act.key && actions()[act.key] ? act.key
      : (actions().pushups ? 'pushups' : '');
    fill(initial);
  }

  /* The pose, in the two shapes that matter: an object by joint name (readable)
     and the 17-slot array by protocol index (what `jointMove` takes). */
  function poseJSON() {
    const byName = {};
    const byIndex = new Array(JOINTS.length).fill(0);
    for (const j of JOINTS) {
      const v = Math.round(st.target[j.name]);
      byName[j.name] = v;
      byIndex[j.index] = v;
    }
    return { byName, byIndex };
  }

  function copyPose() {
    const { byName, byIndex } = poseJSON();
    const text = JSON.stringify(byName, null, 2)
      + '\n\n# index order (jointMove):\n' + JSON.stringify(byIndex);
    const done = () => {
      const el = document.getElementById('copied');
      if (!el) return;
      el.textContent = 'copied';
      setTimeout(() => { el.textContent = ''; }, 1400);
    };
    try {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(done, fallback);
      } else fallback();
    } catch (e) { fallback(); }
    function fallback() {
      // file:// pages often have no clipboard permission; a textarea still works
      const ta = document.createElement('textarea');
      ta.value = text;
      document.body.appendChild(ta);
      ta.select();
      try { document.execCommand('copy'); } catch (e) { /* nothing else to try */ }
      document.body.removeChild(ta);
      done();
    }
  }

  function resize() {
    /* The servo panel is 262 px + margins on the right from 860 px up, so the
       canvas gets what is left. Using innerWidth alone made a 1280 px canvas
       run underneath the panel on a 1400 px window. */
    const panel = window.innerWidth >= 860 ? 286 : 0;
    const w = Math.max(RW, window.innerWidth - panel), h = window.innerHeight;
    const s = Math.max(1, Math.floor(Math.min(w / RW, h / RH)));
    canvas.width = RW * s; canvas.height = RH * s;
    canvas.style.width = (RW * s) + 'px';
    canvas.style.height = (RH * s) + 'px';
  }
  window.addEventListener('resize', resize);
  resize();

  function updateHint() {
    document.getElementById('hint').textContent =
      'PAUSE:' + (sim.paused ? 'ON' : 'OFF') + '  WIRE:' + (sim.wire ? 'ON' : 'OFF') +
      '  MODE:' + sim.mode + '  HUE:' + (sim.hueOn ? 'ON' : 'OFF') +
      '  HUD:' + (sim.hudOn ? 'ON' : 'OFF') +
      '  MUSIC:' + (Music.playing ? 'ON' : 'OFF');
  }

  /* ------------------------------------------------------------- simulate */
  function updateScene(dt) {
    /* A decoded action owns the pose while it runs. It is sampled, not eased:
       the file's keyframes already say how fast each beat is, and rounding
       them through the servo ease would flatten the arm pumps. */
    if (act.playing && act.data) {
      act.t += dt;
      if (act.t * 1000 >= act.data.duration_ms) {
        act.t = act.data.duration_ms / 1000;
        const end = K1.actionPose(act.data, act.t);
        st.target = end;
        st.joints = end;
        stopAction();
        syncMoveInfo();
      } else {
        const now = K1.actionPose(act.data, act.t);
        st.target = now;
        st.joints = now;
      }
      act.panelT += dt;
      if (act.panelT > 0.1) { act.panelT = 0; syncPanel(); syncMoveInfo(); }
    }

    /* Ease each servo toward its target at a fixed fraction per second, so a
       nudge reads as a servo travelling rather than a teleport. The rate is
       roughly what the K1 does at speed 30: fast enough to feel responsive,
       slow enough to see which joint moved. */
    const k = 1 - Math.exp(-dt * 7);
    let moving = 0;
    for (const j of JOINTS) {
      const a = st.joints[j.name], b = st.target[j.name];
      if (Math.abs(b - a) < 0.25) { st.joints[j.name] = b; continue; }
      st.joints[j.name] = a + (b - a) * k;
      moving++;
    }
    if (moving) st.scene = st.scene;    // label already set by the control
    K1.pose(byName, sim.t, sim.phase, st);

    for (const r of sim.rings) {
      if (!r.on) continue;
      r.age += dt * 2.6;
      if (r.age >= 1) r.on = false;
    }
  }

  /* --------------------------------------------------------- world matrix */
  const soleV = new Float32Array(4);

  /* The root transform, and the one place a display-only rotation and slide
     are allowed. `pitch` tips the whole body about the *pelvis* (not about the
     origin, or the robot would slide sideways as it leans), so composing from
     the pelvis down keeps the body and its legs in one rigid piece. `tx` then
     slides that piece along the floor in the robot's own heading, which at yaw
     0 is plain +X. At pitch 0 and tx 0 this is exactly the old T(0, y, 0)
     root. */
  function composeWorld(y, pitch, tx) {
    const h = D.hipH, t = tx || 0;
    M4.compose(rootM, 0, 0, 0, 0, st.yaw, pitch || 0, null);
    // rootM = T(t along the heading, y + h, 0) * R * T(0, -h, 0)
    const fx = Math.cos(st.yaw), fz = -Math.sin(st.yaw);
    rootM[12] = t * fx - rootM[4] * h;
    rootM[13] = y + h - rootM[5] * h;
    rootM[14] = t * fz - rootM[6] * h;
    for (const p of parts) {
      M4.compose(tmpM, p.pos[0], p.pos[1], p.pos[2], p.rot[0], p.rot[1], p.rot[2], null);
      if (p.parent === null) M4.mul(p.world, rootM, tmpM);
      else M4.mul(p.world, byName[p.parent].world, tmpM);
      M4.flattenY(p.flat, p.world);
      M4.normalMat3(p.nm, p.world);
    }
  }
  /* The lowest vertex of one sole, in world space, with its x/z as well as its
     height: the support solve needs to know where the foot is, not just how
     high the model floats. */
  function soleLow(nm) {
    const g = byName[nm].geo.pos, w = byName[nm].world;
    let bx = 0, by = Infinity, bz = 0;
    for (let i = 0; i < g.length; i += 3) {
      M4.xformPoint(soleV, w, g[i], g[i + 1], g[i + 2]);
      if (soleV[1] < by) { by = soleV[1]; bx = soleV[0]; bz = soleV[2]; }
    }
    return { x: bx, y: by, z: bz };
  }
  function lowestSole() {
    return Math.min(soleLow('footL').y, soleLow('footR').y);
  }
  function lowestPalm() {
    return Math.min(soleLow('handL').y, soleLow('handR').y);
  }
  /* The lowest vertex of any part. A seated action is carried by the pelvis
     and legs, not only the soles, so its support point has to be allowed to be
     the calf or the butt. Used only for `contact: 'seat'`, where the whole
     model is lowered onto whatever is lowest. */
  function lowestPart() {
    let low = Infinity;
    for (const p of parts) low = Math.min(low, soleLow(p.name).y);
    return low;
  }

  /* Where each sole stands in the neutral pose, measured along the robot's
     heading. The support solve puts the support sole back here, so an action
     does not wander across the floor: whatever the legs do, the foot carrying
     the body stays where it is standing. Measured once, lazily, in a pose with
     no idle head drift. */
  let soleRef = null;
  function supportRef() {
    if (soleRef) return soleRef;
    const save = parts.map((p) => [p.rot[0], p.rot[1], p.rot[2]]);
    K1.pose(byName, 0, 0, { joints: K1.neutralPose(), idle: false });
    composeWorld(0, 0, 0);
    const fwd = (v) => v.x * Math.cos(st.yaw) + v.z * -Math.sin(st.yaw);
    soleRef = { L: fwd(soleLow('footL')), R: fwd(soleLow('footR')) };
    parts.forEach((p, i) => {
      p.rot[0] = save[i][0]; p.rot[1] = save[i][1]; p.rot[2] = save[i][2];
    });
    return soleRef;
  }

  /* A K1 has no waist servo: pelvis and torso are one rigid piece, and the legs
     are the only thing between the floor and the body. So the honest picture is
     not "pelvis fixed, legs swing" but "support foot fixed, body moves": the
     sole the weight is on stays on the floor, and the pelvis tips and slides
     with the leg chain.

     The pitch itself comes from one of three places:

       feet only (default) - minus the sum of the support leg's pitch servos,
         solved live. That is continuous in the pose, so it needs no smoothing:
         a crouch cancels its own angles and stays upright, a bow tips over.

       hands + feet (`contact: 'hands'`) - a push-up rests on its palms and its
         toes at once, and its hips are straight because it is lying down. The
         same counts read as "standing" if the pitch is solved from the current
         pose alone, and solving each frame would jump between the standing and
         the prone answer. So the pitch is solved at the action's own keyframes,
         where the file says what the body is doing, and then interpolated
         between them exactly like the servo magnitudes. See `contactPitch`.

       seated (`contact: 'seat'`) - a floor sit is not held up by flat soles.
         Solving the standing pitch from the leg chain would tip the torso past
         horizontal and put its head through the floor, and planting a sole
         would fold the legs behind the body. The torso therefore stays upright
         (pitch 0), the pelvis stays over its own origin, and the model is
         lowered until its lowest part - a sole, a calf, the pelvis or a palm -
         rests on the floor. That is a display choice, not a servo value: the
         bytes still own every joint.

     Otherwise the sole is then planted and slid back to where it stands in the
     neutral pose. The caller lifts the whole model only if the *other* foot
     would go through the floor. The clamp stops a nonsense pose (a fall, a
     kick) from spinning the model over. */
  function support() {
    const ref = supportRef();
    composeWorld(0, 0, 0);
    const lL = soleLow('footL'), lR = soleLow('footR');
    const side = lL.y <= lR.y ? 'L' : 'R';
    const foot = 'foot' + side;
    let pitch = -K1.legPitch(byName, side);
    if (act.data && act.data.contact === 'hands') {
      const c = contactPitch(act.data, act.t);
      pitch = c.pitch;
      /* Re-seat the interpolated pitch on the pose that is actually on screen.
         See `contactSeat`; disabled where the action is standing up (weight 0). */
      if (c.weight > 1e-3) {
        pitch += c.weight * (contactSeat(pitch, c.strict) - pitch);
      }
    }
    if (act.data && act.data.contact === 'seat') {
      /* World is still at pitch 0 above; drop it onto its lowest point. */
      composeWorld(0, 0, 0);
      return { pitch: 0, tx: 0, ty: -lowestPart() };
    }
    pitch = clamp(pitch, -2.7, 2.7);
    composeWorld(0, pitch, 0);
    const s = soleLow(foot);
    const fwd = s.x * Math.cos(st.yaw) + s.z * -Math.sin(st.yaw);
    return { pitch, tx: ref[side] - fwd, ty: -s.y };
  }

  /* The body pitch that puts the lowest palm at the same height as the lowest
     sole, in the pose the model is in right now. Scans for every crossing and
     returns the one closest to the flat-foot pitch, because that is the pose
     the legs are already describing; the far ones are "the robot toppled over
     forwards/backwards", which no keyframe asks for. `found` is false where
     there is no such root at all, which is how a standing keyframe is told
     apart from a contact one. */
  function contactPitchHere(flat) {
    const gap = (p) => {
      composeWorld(0, p, 0);
      return Math.min(soleLow('footL').y, soleLow('footR').y) -
        Math.min(soleLow('handL').y, soleLow('handR').y);
    };
    const step = 3 * Math.PI / 180;
    let best = flat, bestD = Infinity, found = false;
    let prevG = gap(-Math.PI), prevP = -Math.PI;
    for (let p = -Math.PI + step; p <= Math.PI + 1e-9; p += step) {
      const g = gap(p);
      if (prevG * g <= 0 && Math.abs(prevG - g) < 0.3) {
        /* the crossing itself, refined by bisection */
        let lo = prevP, hi = p, gLo = prevG;
        for (let i = 0; i < 20; i++) {
          const mid = (lo + hi) * 0.5, gm = gap(mid);
          if (gLo * gm <= 0) hi = mid; else { lo = mid; gLo = gm; }
        }
        const root = (lo + hi) * 0.5, d = Math.abs(root - flat);
        /* `|prevG - g| < 0.3` throws away the tangencies of a limb brushing
           the floor without a real crossing; the distance cap throws away the
           toppled-over roots, which are further from the legs than this. */
        if (d < bestD && d < 1.2) { bestD = d; best = root; found = true; }
      }
      prevG = g; prevP = p;
    }
    return { pitch: best, found };
  }

  /* Nudge a contact pose's pitch by the few degrees that put the lowest palm
     back level with the lowest sole, searching only a narrow window around the
     pitch the keyframes interpolate to. The window is what makes this safe: a
     pose can have several pitches that touch the floor, and this only ever
     considers the one near the branch the action is already on.

     `exact` is true when both bracketing keyframes are themselves contact
     frames: there the palms should stay glued to the floor for the whole
     segment, so the correction runs even when the palm is a hair *above* the
     sole. That is what keeps a linear interpolation of pose and pitch from
     letting the palms bob while the body slides over them. Everywhere else -
     the way down and, more importantly, the push-off - the correction only
     runs when the palm is actually *below* the sole. A nearby root is not
     enough there: the hands are leaving the floor, and pulling the body back
     down to touch a root a few degrees away would turn a smooth push-off into
     a snap. With the penetration as the gate, the correction grows
     continuously from zero, because a pose that exactly touches is itself the
     root. */
  function contactSeat(pitch, exact) {
    const gap = (p) => {
      composeWorld(0, p, 0);
      return Math.min(soleLow('footL').y, soleLow('footR').y) -
        Math.min(soleLow('handL').y, soleLow('handR').y);
    };
    composeWorld(0, pitch, 0);
    const g0 = gap(pitch);
    if (!exact && g0 <= 0) return pitch;
    /* already exactly seated: any root found in the window would be this one */
    if (Math.abs(g0) < 1e-6) return pitch;
    const span = 0.12, step = 0.01;
    let best = pitch, bestD = Infinity;
    let prevP = pitch - span, prevG = gap(prevP);
    for (let p = prevP + step; p <= pitch + span + 1e-9; p += step) {
      const g = gap(p);
      if (g === 0 || (prevG < 0) !== (g < 0)) {
        let lo = prevP, hi = p, gLo = prevG;
        for (let i = 0; i < 14; i++) {
          const mid = (lo + hi) * 0.5, gm = gap(mid);
          if (gLo * gm <= 0) hi = mid; else { lo = mid; gLo = gm; }
        }
        const root = (lo + hi) * 0.5, d = Math.abs(root - pitch);
        if (d < bestD) { bestD = d; best = root; }
      }
      prevP = p; prevG = g;
    }
    return best;
  }

  /* The pitch for a contact action, solved once per keyframe and cached on the
     action, then interpolated at time t like the servo magnitudes. Keyframes
     where the pose is standing (the neutral first and last ones) have no
     contact root near the legs, so they keep the flat-foot pitch and the body
     rises into them smoothly; `weight` is the interpolated 1/0 that says how
     much of the frame is really a two-contact frame. `strict` is 1 only inside
     a segment whose *both* keyframes are contact frames, where the palms must
     stay welded to the floor for the whole segment (see `contactSeat`). */
  function contactPitch(action, t) {
    if (!action.__pitch || action.__pitch.frames !== action.frames) {
      const save = parts.map((p) => [p.rot[0], p.rot[1], p.rot[2]]);
      const values = [], contact = [];
      for (const f of action.frames) {
        K1.pose(byName, 0, 0, { joints: K1.actionPose(action, f.t), idle: false });
        composeWorld(0, 0, 0);
        const side = soleLow('footL').y <= soleLow('footR').y ? 'L' : 'R';
        const here = contactPitchHere(-K1.legPitch(byName, side));
        values.push(here.pitch);
        contact.push(here.found ? 1 : 0);
      }
      parts.forEach((p, i) => {
        p.rot[0] = save[i][0]; p.rot[1] = save[i][1]; p.rot[2] = save[i][2];
      });
      action.__pitch = { frames: action.frames, values, contact };
    }
    const f = action.frames, { values, contact } = action.__pitch;
    if (t <= f[0].t) return { pitch: values[0], weight: contact[0], strict: 0 };
    const last = f.length - 1;
    if (t >= f[last].t) return { pitch: values[last], weight: contact[last], strict: 0 };
    let i = 1;
    while (i < f.length && f[i].t < t) i++;
    const u = (t - f[i - 1].t) / Math.max(1e-6, f[i].t - f[i - 1].t);
    return {
      pitch: values[i - 1] + (values[i] - values[i - 1]) * u,
      weight: contact[i - 1] + (contact[i] - contact[i - 1]) * u,
      strict: contact[i - 1] && contact[i] ? 1 : 0
    };
  }

  /* The pitch actually applied, for the HUD and the harnesses. display only. */
  let bodyPitch = 0;
  function bodyLean() { return bodyPitch; }

  function updateWorld() {
    const s = support();
    composeWorld(s.ty, s.pitch, s.tx);
    bodyPitch = s.pitch;
    /* Safety net: if the pose puts the other foot - or, in a contact action,
       a palm, or in a seated action any part at all - below the floor (a
       kneel, an asymmetric action, a frame halfway between two touching
       keyframes), lift the whole model until nothing is under it. The support
       point then sits a little high, which is what a real limb does when
       another part is on the ground. Display only. */
    const low = act.data && act.data.contact === 'seat'
      ? lowestPart()
      : Math.min(lowestSole(), lowestPalm());
    if (low < 0) composeWorld(s.ty - low, s.pitch, s.tx);
  }

  /* ------------------------------------------------------------- camera */
  function updateCamera(dt) {
    /* A slow drift while nobody is touching anything - it shows the model is
       three-dimensional. The first slider, nudge or drag stops it, because a
       moving camera while you are reading a joint is just noise. */
    if (cam.auto) {
      cam.autoT += dt;
      cam.tYaw = -0.78 + Math.sin(cam.autoT * 0.11) * 0.55;
      cam.tPitch = 0.18 + Math.sin(cam.autoT * 0.07) * 0.10;
      cam.tDist = 2.1 + Math.sin(cam.autoT * 0.05) * 0.3;
    }
    const k = 1 - Math.exp(-dt * 6);
    cam.yaw = lerp(cam.yaw, cam.tYaw, k);
    cam.pitch = lerp(cam.pitch, cam.tPitch, k);
    cam.dist = lerp(cam.dist, cam.tDist, k);
    const eye = [
      cam.target[0] + Math.cos(cam.pitch) * Math.cos(cam.yaw) * cam.dist,
      cam.target[1] + Math.sin(cam.pitch) * cam.dist,
      cam.target[2] + Math.cos(cam.pitch) * Math.sin(cam.yaw) * cam.dist
    ];
    M4.perspective(proj, 46 * DEG, RW / RH, 0.05, 400);
    M4.lookAt(view, eye, cam.target, [0, 1, 0]);
    M4.mul(vp, proj, view);
    M4.invert(invVP, vp);
    return eye;
  }

  /* Flat-colour path, for when the driver rejected the lit robot shader. Same
     geometry, same walk cycle, no rim or specular on the robot.

     It still has to respond to the keys. A demo where W and C look identical to
     a demo where the keys are dead is worse than one that admits it is
     degraded, so: W swaps to the wireframe index buffer, C rotates the part
     colours on the CPU with the same rotation the full shader does, N gets a
     real per-face normal from the varying. The hue rotation lives in M4
     because the flat shader may not afford a hue uniform. */
  const floorM = M4.create();
  const hueOut = new Float32Array(3);

  /* The robot only, flat shaded. The sky, floor, shadow and ring passes are
     independent programs and are unaffected by the lit robot shader being
     rejected, so they are not repeated here - drawScene still runs them. */
  function drawFlatRobot() {
    gl.useProgram(pMin.p);
    gl.uniformMatrix4fv(pMin.u.uVP, false, vp);
    gl.uniform3fv(pMin.u.uLight, PAL.light);
    gl.uniform1f(pMin.u.uMode, sim.wire ? 2 : sim.mode);
    for (const p of parts) {
      GLU.bindMesh(gl, pMin, p.mesh);
      gl.uniformMatrix4fv(pMin.u.uModel, false, p.world);
      /* every generator in geo.js is flat coloured, so the part's own colour
         is simply the first vertex of its colour buffer */
      const c = p.geo.col;
      if (sim.hueOn) M4.hueRotate(hueOut, c, sim.hue);
      else { hueOut[0] = c[0]; hueOut[1] = c[1]; hueOut[2] = c[2]; }
      gl.uniform3f(pMin.u.uColor, hueOut[0], hueOut[1], hueOut[2]);
      if (sim.wire) gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, p.wireBuf);
      gl.drawElements(sim.wire ? gl.LINES : gl.TRIANGLES,
        sim.wire ? p.wireCount : p.mesh.count, gl.UNSIGNED_SHORT, 0);
      if (sim.wire) gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, p.mesh.idx);
    }
  }

  /* A floor stand-in for when the real floor program is gone. Flat, and with
     no normals worth showing, but it gives the robot something to stand on. */
  function drawFlatFloor() {
    gl.useProgram(pMin.p);
    gl.uniformMatrix4fv(pMin.u.uVP, false, vp);
    M4.identity(floorM);
    GLU.bindMesh(gl, pMin, floorMesh);
    gl.uniformMatrix4fv(pMin.u.uModel, false, floorM);
    gl.uniform3f(pMin.u.uColor, PAL.floor[0] * 2.2, PAL.floor[1] * 2.2, PAL.floor[2] * 2.2);
    gl.uniform1f(pMin.u.uMode, 0);
    gl.disable(gl.CULL_FACE);
    gl.drawElements(gl.TRIANGLES, floorMesh.count, gl.UNSIGNED_SHORT, 0);
  }

  /* -------------------------------------------------------------- render */
  function drawScene(eye) {
    gl.bindFramebuffer(gl.FRAMEBUFFER, target.fb);
    gl.viewport(0, 0, RW, RH);
    gl.clearColor(PAL.horizon[0], PAL.horizon[1], PAL.horizon[2], 1);
    gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
    gl.enable(gl.DEPTH_TEST);
    gl.depthFunc(gl.LEQUAL);

    /* Each block is guarded: a rejected program costs its own effect only, and
       the rest keep running. The clear colour above is the horizon, so a missing
       sky shader leaves a flat horizon behind, which beats losing everything. */
    if (pSky) {
      gl.disable(gl.DEPTH_TEST);
      gl.depthMask(false);
      gl.useProgram(pSky.p);
      gl.bindBuffer(gl.ARRAY_BUFFER, quadBuf);
      gl.enableVertexAttribArray(pSky.a.aPos);
      gl.vertexAttribPointer(pSky.a.aPos, 2, gl.FLOAT, false, 0, 0);
      gl.uniformMatrix4fv(pSky.u.uInvVP, false, invVP);
      gl.uniform3fv(pSky.u.uCam, eye);
      gl.uniform1f(pSky.u.uTime, sim.t);
      gl.uniform3fv(pSky.u.uZenith, PAL.zenith);
      gl.uniform3fv(pSky.u.uHorizon, PAL.horizon);
      gl.uniform3fv(pSky.u.uSunCol, PAL.sun);
      gl.uniform1f(pSky.u.uBands, 26.0);
      gl.drawArrays(gl.TRIANGLES, 0, 3);
      gl.depthMask(true);
      gl.enable(gl.DEPTH_TEST);
    }

    if (pFloor) {
    /* floor */
    gl.useProgram(pFloor.p);
    GLU.bindMesh(gl, pFloor, floorMesh);
    M4.identity(tmpM);
    gl.uniformMatrix4fv(pFloor.u.uVP, false, vp);
    gl.uniformMatrix4fv(pFloor.u.uModel, false, tmpM);
    gl.uniform3fv(pFloor.u.uCam, eye);
    const fx = Math.cos(st.yaw), fz = Math.sin(st.yaw);
    gl.uniform2f(pFloor.u.uFwd, fx, fz);
    gl.uniform1f(pFloor.u.uOffset, sim.ground);
    gl.uniform1f(pFloor.u.uFog, 1.0);
    gl.uniform3fv(pFloor.u.uFogCol, PAL.horizon);
    gl.uniform3fv(pFloor.u.uBase, PAL.floor);
    gl.uniform3fv(pFloor.u.uLine, PAL.line);
    gl.uniform1f(pFloor.u.uTime, sim.t);
    gl.disable(gl.CULL_FACE);
    gl.drawElements(gl.TRIANGLES, floorMesh.count, gl.UNSIGNED_SHORT, 0);
    } else if (minimal) {
      drawFlatFloor();
    }

    if (pShadow) {
    /* planar shadow */
    gl.enable(gl.BLEND);
    gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);
    gl.depthMask(false);
    gl.enable(gl.POLYGON_OFFSET_FILL);
    gl.polygonOffset(-1.5, -2.0);
    gl.useProgram(pShadow.p);
    gl.uniformMatrix4fv(pShadow.u.uVP, false, vp);
    gl.uniform3fv(pShadow.u.uCam, eye);
    gl.uniform1f(pShadow.u.uFog, 1.0);
    gl.uniform3fv(pShadow.u.uFogCol, PAL.horizon);
    gl.uniform1f(pShadow.u.uDark, 0.75);
    for (const p of parts) {
      GLU.bindMesh(gl, pShadow, p.mesh);
      gl.uniformMatrix4fv(pShadow.u.uModel, false, p.flat);
      gl.uniformMatrix3fv(pShadow.u.uNrm, false, p.nm);
      gl.drawElements(gl.TRIANGLES, p.mesh.count, gl.UNSIGNED_SHORT, 0);
    }
    gl.disable(gl.POLYGON_OFFSET_FILL);
    gl.depthMask(true);
    }

    if (pRing) {
    /* impact rings */
    gl.blendFunc(gl.SRC_ALPHA, gl.ONE);
    gl.depthMask(false);
    gl.useProgram(pRing.p);
    gl.uniformMatrix4fv(pRing.u.uVP, false, vp);
    gl.uniform3fv(pRing.u.uCol, PAL.ring);
    gl.bindBuffer(gl.ARRAY_BUFFER, ringMesh.pos);
    gl.enableVertexAttribArray(pRing.a.aPos);
    gl.vertexAttribPointer(pRing.a.aPos, 3, gl.FLOAT, false, 0, 0);
    gl.bindBuffer(gl.ARRAY_BUFFER, ringMesh.life);
    gl.enableVertexAttribArray(pRing.a.aLife);
    gl.vertexAttribPointer(pRing.a.aLife, 2, gl.FLOAT, false, 0, 0);
    gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, ringMesh.idx);
    for (const r of sim.rings) {
      if (!r.on) continue;
      ringLifeData[0] = r.age; ringLifeData[1] = r.seed;
      gl.bufferSubData(gl.ARRAY_BUFFER, 0, ringLifeData);
      gl.uniform3f(pRing.u.uOrigin, r.x, 0, r.z);
      gl.uniform1f(pRing.u.uRadius, 0.55 + r.age * 0.5);
      gl.drawElements(gl.TRIANGLES, 6, gl.UNSIGNED_SHORT, 0);
    }
    gl.depthMask(true);
    gl.disable(gl.BLEND);
    }

    /* robot - the only pass that depends on the lit shader, so it is the only
       one that can be flat */
    if (minimal) {
      drawFlatRobot();
      return;
    }
    gl.useProgram(pScene.p);
    gl.uniformMatrix4fv(pScene.u.uVP, false, vp);
    gl.uniform3fv(pScene.u.uCam, eye);
    gl.uniform3fv(pScene.u.uLight, PAL.light);
    gl.uniform3fv(pScene.u.uFogCol, PAL.horizon);
    gl.uniform1f(pScene.u.uFog, 0.55);
    gl.uniform1f(pScene.u.uHue, sim.hueOn ? sim.hue : 0);
    gl.uniform1f(pScene.u.uMode, sim.wire ? 2 : sim.mode);
    const mode = sim.wire ? gl.LINES : gl.TRIANGLES;
    for (const p of parts) {
      GLU.bindMesh(gl, pScene, p.mesh);
      gl.uniformMatrix4fv(pScene.u.uModel, false, p.world);
      gl.uniformMatrix3fv(pScene.u.uNrm, false, p.nm);
      if (sim.wire) gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, p.wireBuf);
      gl.drawElements(mode, sim.wire ? p.wireCount : p.mesh.count, gl.UNSIGNED_SHORT, 0);
      if (sim.wire) gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, p.mesh.idx);
    }
  }

  function drawPost() {
    gl.bindFramebuffer(gl.FRAMEBUFFER, null);
    gl.viewport(0, 0, canvas.width, canvas.height);
    gl.disable(gl.DEPTH_TEST);
    if (!pPost) {
      // No post pass: blit the scene and composite the HUD over it. Two
      // textures, two orientations - the scene FBO is already in GL space and
      // must not be flipped, the HUD canvas must be. See FS_BLIT.
      gl.useProgram(pBlit.p);
      gl.bindBuffer(gl.ARRAY_BUFFER, quadBuf);
      gl.enableVertexAttribArray(pBlit.a.aPos);
      gl.vertexAttribPointer(pBlit.a.aPos, 2, gl.FLOAT, false, 0, 0);
      gl.activeTexture(gl.TEXTURE0);
      gl.bindTexture(gl.TEXTURE_2D, target.tex);
      gl.uniform1i(pBlit.u.uTex, 0);
      gl.uniform1f(pBlit.u.uFlip, 0);
      gl.drawArrays(gl.TRIANGLES, 0, 3);
      if (sim.hudOn) {
        gl.enable(gl.BLEND);
        gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);
        gl.bindTexture(gl.TEXTURE_2D, hudTex.tex);
        gl.uniform1i(pBlit.u.uTex, 0);
        gl.uniform1f(pBlit.u.uFlip, 1);
        gl.drawArrays(gl.TRIANGLES, 0, 3);
        gl.disable(gl.BLEND);
      }
      gl.activeTexture(gl.TEXTURE0);
      return;
    }
    gl.useProgram(pPost.p);
    gl.activeTexture(gl.TEXTURE0);
    gl.bindTexture(gl.TEXTURE_2D, target.tex);
    gl.uniform1i(pPost.u.uScene, 0);
    gl.activeTexture(gl.TEXTURE1);
    gl.bindTexture(gl.TEXTURE_2D, hudTex.tex);
    gl.uniform1i(pPost.u.uHud, 1);
    gl.bindBuffer(gl.ARRAY_BUFFER, quadBuf);
    gl.enableVertexAttribArray(pPost.a.aPos);
    gl.vertexAttribPointer(pPost.a.aPos, 2, gl.FLOAT, false, 0, 0);
    gl.uniform2f(pPost.u.uRes, canvas.width, canvas.height);
    gl.uniform1f(pPost.u.uTime, sim.t);
    gl.uniform1f(pPost.u.uLevels, sim.levels);
    gl.uniform1f(pPost.u.uDither, sim.dither);
    gl.uniform1f(pPost.u.uScan, sim.scan);
    gl.uniform1f(pPost.u.uMask, sim.mask);
    gl.uniform1f(pPost.u.uAberr, sim.aberr * 0.02);
    gl.uniform1f(pPost.u.uFlash, Music.playing ? Music.beat() * 0.035 : 0);
    gl.uniform1f(pPost.u.uHudOn, sim.hudOn ? 1 : 0);
    gl.drawArrays(gl.TRIANGLES, 0, 3);
    gl.activeTexture(gl.TEXTURE0);
  }

  /* ----------------------------------------------------------------- loop */
  let last = performance.now() / 1000, fpsT = 0, fpsN = 0;
  function frame() {
    const now = performance.now() / 1000;
    let dt = Math.min(0.05, now - last);
    last = now;
    if (!sim.paused) {
      sim.t += dt;
      sim.hue = (sim.hue + dt * 0.35) % (Math.PI * 2);
      updateScene(dt);
    }
    updateWorld();
    const eye = updateCamera(dt);
    drawScene(eye);

    /* HUD */
    fpsN++; fpsT += dt;
    if (fpsT > 0.4) { sim.fps = Math.round(fpsN / fpsT); fpsN = 0; fpsT = 0; }
    if (sim.hudOn) {
      HUD.draw(sim.t, dt, {
        left: 'K1 V1.0  ' + Math.round(sim.fps) + 'FPS  ' + tris + 'TRIS  ' + (RW + 'X' + RH)
          + (minimal ? '  *FLAT*' : ''),
        right: 'JOINT ' + ('0' + JOINTS[st.sel].index).slice(-2) + ' '
          + JOINTS[st.sel].name.toUpperCase() + '  '
          + Math.round(st.target[JOINTS[st.sel].name]) + '/255'
          + (Music.playing ? '  PAULA ' + Music.bpm + 'BPM' : ''),
        scene: st.scene,
        level: Music.playing ? Math.max(Music.beat(), 0.15) : 0.0,
        showKeys: sim.showKeys,
        /* the fallback notice must not swallow the mode indicator, or the
           controls look dead even once they are not */
        big: sim.paused ? '*** PAUSED ***'
          : (sim.wire ? 'WIREFRAME' : (sim.mode === 2 ? 'MONO LIGHT'
            : (sim.mode === 1 ? 'NORMAL SHADE' : (sim.hueOn ? 'CYCLE' : null)))),
        bigColor: sim.paused ? '#ff5a3c' : '#54e8ff',
        keys: []
      });
      GLU.uploadCanvas(gl, hudTex);
    }
    drawPost();
    sim.frames++;
    requestAnimationFrame(frame);
  }

  /* ---------------------------------------------------------------- intro */
  /* `#run` starts without a click (bookmark, kiosk, headless screenshot).
     `#run/pushups/0.86` goes further: it starts that decoded action and holds
     the pose at that second, which is how a single keyframe can be inspected
     without playing the whole take. */
  function hashAction() {
    const h = (window.location.hash || '').replace(/^#/, '').split('/');
    if (h[0] !== 'run' || !h[1]) return false;
    if (!playAction(decodeURIComponent(h[1]))) return false;
    const at = Number(h[2]);
    if (h[2] && Number.isFinite(at)) {
      act.t = clamp(at, 0, act.data.duration_ms / 1000);
      const p = K1.actionPose(act.data, act.t);
      st.target = p;
      st.joints = p;
      act.playing = false;                // a still, not a take
      syncMoveInfo();
    }
    return true;
  }

  const intro = document.getElementById('intro');
  function begin() {
    intro.classList.add('gone');
    if (!started) {
      started = true;
      buildPanel();                        // 17 sliders + the phone's move list
      /* Music is off by default: this page is for inspecting poses, and a
         chiptune starting on the first click is noise. M starts/stops it
         explicitly, from a real keypress, so autoplay policy stays happy. */
      hashAction();
    }
  }
  let started = false;
  intro.addEventListener('click', begin);
  intro.addEventListener('touchend', begin);
  window.addEventListener('keydown', (e) => { if (!started) begin(); });
  /* #run starts without a click: useful for a bookmark, a kiosk, or a
     headless screenshot. Sound still waits for whatever the browser allows. */
  if ((window.location.hash || '').indexOf('#run') === 0) begin();

  updateHint();
  window.__K1_BOOTED = true;           // checked by the load guard in index.html

  /* The harness checks that the panel, the presets and the exported pose all
     agree with K1.JOINTS. It is also the quickest way to poke a joint from the
     browser console. Nothing reads this at runtime. */
  window.K1DEMO = {
    st, JOINTS, PRESETS, applyPreset, poseJSON, nudge, selectJoint,
    act, actions, actionList, playAction, stopAction,
    camera: cam, cameraPresets: CAM_PRESETS,
    lean: () => bodyLean(),
    /* Where the rig actually ended up, in world space. The harnesses and the
       screenshot probes use this to measure contact - a hand on the floor, a
       sole on the floor - instead of looking at pixels. */
    measure: () => parts.map((p) => {
      let lo = Infinity, hi = -Infinity, lx = 0, lz = 0;
      for (let i = 0; i < p.geo.pos.length; i += 3) {
        M4.xformPoint(soleV, p.world, p.geo.pos[i], p.geo.pos[i + 1], p.geo.pos[i + 2]);
        if (soleV[1] < lo) { lo = soleV[1]; lx = soleV[0]; lz = soleV[2]; }
        if (soleV[1] > hi) hi = soleV[1];
      }
      return {
        name: p.name, x: p.world[12], y: p.world[13], z: p.world[14],
        low: lo, high: hi, lowAt: [lx, lo, lz]
      };
    })
  };

  requestAnimationFrame(frame);
})();
