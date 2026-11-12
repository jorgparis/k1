/* hud.js - 320x200 1-bit overlay (copper bars, marquee, vu meter) */
(function (global) {
  'use strict';

  const W = 320, H = 200;
  const HUD = {
    W, H,
    canvas: document.createElement('canvas'),
    scratch: document.createElement('canvas'),
    ctx: null,
    on: true,
    marquee: '   *** W A L K E R   ***  RAW WEBGL / FLAT SHADED LOW POLY / PROCEDURAL 2-BONE IK WALK CYCLE / GREETINGS TO ATARI XL AND AMIGA SCENE ***   ',
    scroll: 0
  };
  HUD.canvas.width = W; HUD.canvas.height = H;
  HUD.scratch.width = W; HUD.scratch.height = H;
  const ctx = HUD.ctx = HUD.canvas.getContext('2d');
  const scr = HUD.scratch.getContext('2d');
  const data = new Uint8ClampedArray(W * H * 4);
  const sdata = new Uint8ClampedArray(W * H * 4);
  const img = new ImageData(data, W, H);
  const simg = new ImageData(sdata, W, H);

  const AMBER = '#ffb03c', AMBER2 = '#ff8a1e', CYAN = '#54e8ff', WHITE = '#e8f4ff',
    GREY = '#7a8698', DARK = '#0a0c12', RED = '#ff5a3c', GREEN = '#7dff8a';

  function bar(x, y, w, h, c1, c2) {
    const g = scr.createLinearGradient(0, y, 0, y + h);
    g.addColorStop(0, c1); g.addColorStop(1, c2);
    scr.fillStyle = g; scr.fillRect(x, y, w, h);
  }

  /* main draw; info = {left, right, scene, fps, polys, keys[], level, loading} */
  function draw(t, dt, info) {
    scr.clearRect(0, 0, W, H);
    scr.textBaseline = 'top';

    /* ---- top copper bar + marquee ---- */
    bar(0, 0, W, 15, '#e07818', '#7c3208');
    scr.fillStyle = DARK; scr.fillRect(0, 15, W, 1);
    HUD.scroll -= 18 * dt;
    scr.save();
    scr.beginPath(); scr.rect(6, 0, W - 12, 15); scr.clip();
    scr.font = 'bold 9px "DejaVu Sans Mono", "Liberation Mono", monospace';
    scr.fillStyle = '#1a0c02';
    scr.fillText(HUD.marquee, W - 6 + HUD.scroll, 3);
    scr.restore();
    // side blocks
    scr.fillStyle = DARK; scr.fillRect(0, 0, 5, 15); scr.fillRect(W - 5, 0, 5, 15);
    scr.fillStyle = AMBER;
    scr.fillRect(0, 0, 1, 15); scr.fillRect(W - 1, 0, 1, 15);

    /* ---- bottom bar ---- */
    bar(0, H - 13, W, 13, '#20304c', '#0b1220');
    scr.fillStyle = CYAN; scr.fillRect(0, H - 14, W, 1);
    scr.font = '8px "DejaVu Sans Mono", "Liberation Mono", monospace';
    scr.fillStyle = WHITE; scr.fillText(info.left, 4, H - 11);
    const rw = scr.measureText(info.right).width;
    scr.fillStyle = AMBER; scr.fillText(info.right, W - 4 - rw, H - 11);

    /* ---- scene name, big-ish, bottom left above the bar ---- */
    scr.font = 'bold 9px "DejaVu Sans Mono", "Liberation Mono", monospace';
    scr.fillStyle = DARK;
    scr.fillRect(4, H - 30, 8 + info.scene.length * 5.4, 11);
    scr.fillStyle = AMBER; scr.fillText(info.scene, 8, H - 28);

    /* ---- vu meter ---- */
    const vx = W - 46, vy = H - 34;
    scr.fillStyle = DARK; scr.fillRect(vx - 2, vy - 2, 42, 13);
    for (let i = 0; i < 12; i++) {
      const on = info.level * 12 > i;
      scr.fillStyle = on ? (i > 8 ? RED : i > 5 ? AMBER : GREEN) : '#1a2230';
      scr.fillRect(vx + i * 3.2, vy, 2.4, 9);
    }
    scr.fillStyle = GREY; scr.font = '8px "DejaVu Sans Mono", monospace';
    scr.fillText('L/R', vx + 2, vy - 10);

    /* ---- left edge vertical text ---- */
    scr.save();
    scr.translate(4, H - 18);
    scr.rotate(-Math.PI / 2);
    scr.font = 'bold 8px "DejaVu Sans Mono", monospace';
    scr.fillStyle = 'rgba(84,232,255,0.55)';
    const vtext = 'DEMO BY OPENCODE - GREETS THE DEMO SCENE ';
    scr.fillText(vtext, 0, -8);
    scr.restore();

    /* ---- key hints ---- */
    if (info.showKeys) {
      scr.font = '8px "DejaVu Sans Mono", monospace';
      const k = info.keys;
      let x = (W - 200) / 2;
      const y = H - 46;
      scr.fillStyle = 'rgba(8,10,16,0.75)'; scr.fillRect(x - 6, y - 3, 212, 12);
      scr.fillStyle = CYAN;
      scr.fillText('SPACE PAUSE  R RESET  M MUSIC  W WIRE  C CYCLE  F FULL  1-3 CAM  MOUSE ORBIT', x, y);
    }

    /* ---- corner debug / big overlay text ---- */
    if (info.big) {
      scr.font = 'bold 12px "DejaVu Sans Mono", monospace';
      const tw = scr.measureText(info.big).width;
      scr.fillStyle = 'rgba(8,10,16,0.8)';
      scr.fillRect(W / 2 - tw / 2 - 6, 24, tw + 12, 16);
      scr.fillStyle = info.bigColor || AMBER;
      scr.fillText(info.big, W / 2 - tw / 2, 26);
    }

    /* ---- threshold the whole thing to 1 bit ---- */
    sdata.set(scr.getImageData(0, 0, W, H).data);
    for (let i = 0; i < data.length; i += 4) {
      if (sdata[i + 3] > 96) { data[i] = sdata[i]; data[i + 1] = sdata[i + 1]; data[i + 2] = sdata[i + 2]; data[i + 3] = 255; }
      else { data[i + 3] = 0; }
    }
    ctx.putImageData(img, 0, 0);
  }

  HUD.draw = draw;
  global.HUD = HUD;
})(window);
