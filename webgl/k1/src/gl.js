/* gl.js - small WebGL1 helper layer: programs, buffers, FBO, textures */
(function (global) {
  'use strict';

  function compile(gl, type, src, name) {
    const sh = gl.createShader(type);
    gl.shaderSource(sh, src);
    gl.compileShader(sh);
    if (!gl.getShaderParameter(sh, gl.COMPILE_STATUS)) {
      const log = gl.getShaderInfoLog(sh);
      const numbered = src.split('\n').map((l, i) => (i + 1) + ': ' + l).join('\n');
      throw new Error('shader compile failed [' + name + ']\n' + log + '\n' + numbered);
    }
    return sh;
  }

  function program(gl, vsSrc, fsSrc, name) {
    // Test hook: several programs legitimately share a source (scene and shadow
    // both use VS_SCENE, post and blit use the same fullscreen triangle). That
    // is correct for a driver, but it means a harness cannot reject one program
    // by its source alone. Publishing the name lets a test target exactly one.
    gl.__k1Program = name;
    const vs = compile(gl, gl.VERTEX_SHADER, vsSrc, name + '.vs');
    const fs = compile(gl, gl.FRAGMENT_SHADER, fsSrc, name + '.fs');
    const p = gl.createProgram();
    gl.attachShader(p, vs); gl.attachShader(p, fs);
    gl.linkProgram(p);
    if (!gl.getProgramParameter(p, gl.LINK_STATUS)) {
      throw new Error('link failed [' + name + '] ' + gl.getProgramInfoLog(p));
    }
    gl.deleteShader(vs); gl.deleteShader(fs);
    const u = {};
    const n = gl.getProgramParameter(p, gl.ACTIVE_UNIFORMS);
    for (let i = 0; i < n; i++) {
      const info = gl.getActiveUniform(p, i);
      const nm = info.name.replace(/\[0\]$/, '');
      u[nm] = gl.getUniformLocation(p, nm);
    }
    const a = {};
    const na = gl.getProgramParameter(p, gl.ACTIVE_ATTRIBUTES);
    for (let i = 0; i < na; i++) {
      const info = gl.getActiveAttrib(p, i);
      a[info.name] = gl.getAttribLocation(p, info.name);
    }
    return { p, u, a, name };
  }

  /* interleaved-ish buffers: separate VBOs, one VAO per mesh+program combo is overkill,
     we just bind attributes each draw */
  function makeMesh(gl, buffers) {
    const m = {};
    m.pos = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, m.pos);
    gl.bufferData(gl.ARRAY_BUFFER, buffers.pos, gl.STATIC_DRAW);
    m.nrm = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, m.nrm);
    gl.bufferData(gl.ARRAY_BUFFER, buffers.nrm, gl.STATIC_DRAW);
    m.col = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, m.col);
    gl.bufferData(gl.ARRAY_BUFFER, buffers.col, gl.STATIC_DRAW);
    m.idx = gl.createBuffer();
    gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, m.idx);
    gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, buffers.idx, gl.STATIC_DRAW);
    m.count = buffers.idx.length;
    m.u32 = buffers.idx instanceof Uint32Array;
    return m;
  }

  function bindMesh(gl, prog, mesh) {
    const a = prog.a;
    function att(name, buf, size) {
      const loc = a[name];
      if (loc === undefined || loc < 0) return;
      gl.bindBuffer(gl.ARRAY_BUFFER, buf);
      gl.enableVertexAttribArray(loc);
      gl.vertexAttribPointer(loc, size, gl.FLOAT, false, 0, 0);
    }
    att('aPos', mesh.pos, 3);
    if (a['aNormal'] !== undefined) att('aNormal', mesh.nrm, 3);
    if (a['aColor'] !== undefined) att('aColor', mesh.col, 4);
    gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, mesh.idx);
  }

  function createTarget(gl, w, h) {
    const tex = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, tex);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, w, h, 0, gl.RGBA, gl.UNSIGNED_BYTE, null);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    const depth = gl.createRenderbuffer();
    gl.bindRenderbuffer(gl.RENDERBUFFER, depth);
    gl.renderbufferStorage(gl.RENDERBUFFER, gl.DEPTH_COMPONENT16, w, h);
    const fb = gl.createFramebuffer();
    gl.bindFramebuffer(gl.FRAMEBUFFER, fb);
    gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, tex, 0);
    gl.framebufferRenderbuffer(gl.FRAMEBUFFER, gl.DEPTH_ATTACHMENT, gl.RENDERBUFFER, depth);
    gl.bindFramebuffer(gl.FRAMEBUFFER, null);
    return { fb, tex, depth, w, h };
  }

  /* dynamic texture uploaded from a 2d canvas (HUD) */
  function createCanvasTexture(gl, w, h) {
    const tex = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, tex);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, w, h, 0, gl.RGBA, gl.UNSIGNED_BYTE, null);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    return { tex, w, h, canvas: document.createElement('canvas') };
  }

  function uploadCanvas(gl, ct) {
    if (ct.canvas.width !== ct.w) ct.canvas.width = ct.w;
    if (ct.canvas.height !== ct.h) ct.canvas.height = ct.h;
    gl.bindTexture(gl.TEXTURE_2D, ct.tex);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, ct.canvas);
  }

  function fullscreenQuad(gl) {
    const b = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, b);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW);
    return b;
  }

  global.GLU = { program, makeMesh, bindMesh, createTarget, createCanvasTexture, uploadCanvas, fullscreenQuad };
})(window);
