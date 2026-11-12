/* music.js - procedural chiptune (2 pulse + sub + noise) with a lookahead scheduler */
(function (global) {
  'use strict';

  const BPM = 150;                 // 2 beats per walk cycle -> footfalls land on the kick
  const STEP = 60 / BPM / 4;       // 16th note
  const LOOKAHEAD = 0.12;

  const N = {                       // note name -> midi
    C: 0, D: 2, E: 4, F: 5, G: 7, A: 9, B: 11
  };
  function midi(s) {
    const m = /^([A-G])([#b]?)(-?\d)$/.exec(s);
    if (!m) return -1;
    let v = N[m[1]] + (m[2] === '#' ? 1 : m[2] === 'b' ? -1 : 0);
    return v + (parseInt(m[3], 10) + 1) * 12;
  }
  const hz = (m) => 440 * Math.pow(2, (m - 69) / 12);

  /* ------------------------------------------------------------- patterns */
  const CHORDS = [
    ['A2', 'A3', 'C4', 'E4'],
    ['F2', 'F3', 'A3', 'C4'],
    ['C3', 'C4', 'E4', 'G4'],
    ['G2', 'G3', 'B3', 'D4']
  ];
  const BASS = [
    'A2 . A2 . A2 . E3 . A2 . A2 . G2 . A2 .',
    'F2 . F2 . F2 . C3 . F2 . F2 . E2 . F2 .',
    'C3 . C3 . C3 . G3 . C3 . C3 . B2 . C3 .',
    'G2 . G2 . G2 . D3 . G2 . G2 . A2 . G2 .'
  ];
  const LEAD = [
    'A4 . C5 . E5 . C5 . A4 . G4 . . . E4 . . . .',
    'F4 . A4 . C5 . A4 . F4 . E4 . . . C4 . . . .',
    'C5 . E5 . G5 . E5 . C5 . D5 . . . B4 . . . .',
    'E5 . D5 . C5 . B4 . A4 . . . . . . . . . .'
  ];
  const ARP_ORDER = [1, 2, 3, 2, 1, 2, 3, 2, 3, 2, 1, 2, 1, 3, 2, 1];
  const KICK = '1...1...1...1...';
  const SNARE = '....1.......1...';
  const HAT = '..1...1...1...1.';

  function seq(str) { return str.trim().split(/\s+/); }

  /* ---------------------------------------------------------------- engine */
  let ctx = null, master = null, lp = null, comp = null, timer = null;
  let wave12, wave25, wave50, noiseBuf;
  let step = 0, nextTime = 0, playing = false, startedAt = 0;
  let gain = 1, failed = false;

  function pulseWave(duty) {
    const n = 24, real = new Float32Array(n), imag = new Float32Array(n);
    for (let i = 1; i < n; i++) {
      real[i] = (2 / (i * Math.PI)) * Math.sin(i * Math.PI * duty);
      imag[i] = (2 / (i * Math.PI)) * (1 - Math.cos(i * Math.PI * duty));
    }
    return ctx.createPeriodicWave(real, imag, { disableNormalization: false });
  }

  function build() {
    /* Audio is optional. Autoplay policy, a missing vendor prefix, or a locked
       down context must degrade to silence, never propagate: start() is called
       from a click handler and a throw here would abort the demo. */
    const AC = global.AudioContext || global.webkitAudioContext;
    if (!AC) { failed = true; return false; }
    try {
      ctx = new AC();
      master = ctx.createGain();
      master.gain.value = gain;
      comp = ctx.createDynamicsCompressor();
      comp.threshold.value = -14; comp.knee.value = 12; comp.ratio.value = 6;
      comp.attack.value = 0.003; comp.release.value = 0.18;
      lp = ctx.createBiquadFilter();
      lp.type = 'lowpass'; lp.frequency.value = 5200; lp.Q.value = 0.7;
      master.connect(lp); lp.connect(comp); comp.connect(ctx.destination);

      wave12 = pulseWave(0.125);
      wave25 = pulseWave(0.25);
      wave50 = pulseWave(0.5);

      const len = Math.floor(ctx.sampleRate * 1.0);
      noiseBuf = ctx.createBuffer(1, len, ctx.sampleRate);
      const d = noiseBuf.getChannelData(0);
      let reg = 22222;
      for (let i = 0; i < len; i++) {
        reg = (reg * 1103515245 + 12345) & 0x7fffffff;
        d[i] = (reg / 0x40000000) - 1;
      }
      return true;
    } catch (err) {
      console.warn('audio unavailable, running silent:', err);
      ctx = null; master = null; lp = null; comp = null;
      wave12 = wave25 = wave50 = noiseBuf = null;
      failed = true;
      return false;
    }
  }

  function env(t, a, dur, v, curve) {
    const g = ctx.createGain();
    g.gain.setValueAtTime(0.0001, t);
    g.gain.linearRampToValueAtTime(v, t + a);
    if (curve) g.gain.exponentialRampToValueAtTime(0.0001, t + dur);
    else g.gain.linearRampToValueAtTime(0.0001, t + dur);
    g.connect(master);
    return g;
  }

  function pulse(freq, t, dur, vol, wave, vib, detune) {
    const o = ctx.createOscillator();
    o.setPeriodicWave(wave);
    o.frequency.value = freq;
    if (detune) o.detune.value = detune;
    const g = env(t, 0.004, dur, vol, true);
    if (vib) {
      const lfo = ctx.createOscillator(), la = ctx.createGain();
      lfo.frequency.value = 6.2; la.gain.value = vib;
      lfo.connect(la); la.connect(o.detune);
      lfo.start(t); lfo.stop(t + dur + 0.05);
    }
    o.connect(g); o.start(t); o.stop(t + dur + 0.05);
  }

  function noise(t, dur, vol, type, freq, q) {
    const s = ctx.createBufferSource();
    s.buffer = noiseBuf;
    s.loop = true;
    s.playbackRate.value = 0.7 + Math.random() * 0.6;
    const f = ctx.createBiquadFilter();
    f.type = type; f.frequency.value = freq; f.Q.value = q || 1;
    const g = env(t, 0.002, dur, vol, true);
    s.connect(f); f.connect(g);
    s.start(t); s.stop(t + dur + 0.05);
  }

  function kick(t) {
    const o = ctx.createOscillator();
    o.type = 'sine';
    o.frequency.setValueAtTime(150, t);
    o.frequency.exponentialRampToValueAtTime(42, t + 0.09);
    const g = env(t, 0.002, 0.22, 0.9, true);
    o.connect(g); o.start(t); o.stop(t + 0.3);
    // duck the filter on the beat
    lp.frequency.cancelScheduledValues(t);
    lp.frequency.setValueAtTime(1200, t);
    lp.frequency.linearRampToValueAtTime(5600, t + 0.13);
  }

  function schedule(s, t) {
    const bar = (((s / 16) | 0) % 4 + 4) % 4;
    const b = s % 16;
    const chord = CHORDS[bar];
    const bpat = seq(BASS[bar])[b];
    const lpat = seq(LEAD[bar])[b];

    if (bpat && bpat !== '.') pulse(hz(midi(bpat)), t, STEP * 1.7, 0.34, wave50, 0, -1200);
    if (bpat && bpat !== '.' && b % 4 === 0) pulse(hz(midi(bpat)) * 0.5, t, STEP * 3, 0.22, wave12);

    // arpeggio
    const arpN = midi(chord[ARP_ORDER[b]]);
    if (b % 1 === 0) pulse(hz(arpN) * 2, t, STEP * 0.85, 0.10, wave12, 0, 300);

    if (lpat && lpat !== '.') pulse(hz(midi(lpat)), t, STEP * 3.2, 0.24, wave25, 5, 0);

    if (KICK[b] === '1') kick(t);
    if (SNARE[b] === '1') { noise(t, 0.14, 0.24, 'bandpass', 1900, 0.9); pulse(220, t, 0.06, 0.10, wave50); }
    if (HAT[b] === '1') noise(t, 0.035, 0.09, 'highpass', 8000, 0.7);
  }

  function tick() {
    if (!ctx) { playing = false; return; }
    try {
      while (nextTime < ctx.currentTime + LOOKAHEAD) {
        if (nextTime > ctx.currentTime - 0.05) schedule(step, nextTime);
        nextTime += STEP;
        step++;
      }
    } catch (err) {
      // a scheduled node went stale; keep the visual demo alive
      console.warn('audio scheduler stopped:', err);
      playing = false;
      clearInterval(timer);
    }
  }

  const Music = {
    get playing() { return playing; },
    get bpm() { return BPM; },
    get available() { return !failed; },
    start() {
      if (failed) return false;
      if (!ctx && !build()) return false;
      try {
        if (ctx.state === 'suspended' && ctx.resume) ctx.resume();
      } catch (err) { /* resume is best effort */ }
      if (playing) return true;
      playing = true;
      step = 0;
      nextTime = ctx.currentTime + 0.08;
      startedAt = ctx.currentTime;
      timer = setInterval(tick, 25);
      tick();
      return true;
    },
    stop() {
      if (!playing) return;
      playing = false;
      clearInterval(timer);
    },
    toggle() { if (playing) { this.stop(); return false; } this.start(); return playing; },
    /* servo thump synced to the footfalls */
    step(vel) {
      if (!playing || !ctx) return;
      try {
        const t = ctx.currentTime + 0.005;
        const v = 0.10 + 0.16 * Math.min(1, vel);
        noise(t, 0.055, v, 'lowpass', 420 + Math.random() * 120, 1.4);
        noise(t + 0.012, 0.035, v * 0.55, 'bandpass', 2600, 2.0);
      } catch (err) { /* footfall thump is cosmetic */ }
    },
    setGain(g) { gain = g; if (master && ctx) master.gain.setTargetAtTime(g, ctx.currentTime, 0.05); },
    /* 0..1 pulse on every kick, for the HUD / post flash */
    beat() {
      if (!playing || !ctx) return 0;
      const t = (ctx.currentTime - startedAt) / STEP;
      const f = t - Math.floor(t);
      return Math.max(0, 1 - f * 4) * (((Math.floor(t) % 2) === 0) ? 1 : 0.55);
    },
    bar() { return ctx ? Math.floor((ctx.currentTime - startedAt) / (STEP * 16)) : 0; }
  };

  global.Music = Music;
})(window);
