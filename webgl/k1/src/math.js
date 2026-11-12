/* math.js - tiny column-major 4x4 / 3x3 matrix + vector helpers (GL compatible) */
(function (global) {
  'use strict';

  const M4 = {
    create() {
      const m = new Float32Array(16);
      m[0] = m[5] = m[10] = m[15] = 1;
      return m;
    },
    identity(o) {
      o[0] = 1; o[1] = 0; o[2] = 0; o[3] = 0;
      o[4] = 0; o[5] = 1; o[6] = 0; o[7] = 0;
      o[8] = 0; o[9] = 0; o[10] = 1; o[11] = 0;
      o[12] = 0; o[13] = 0; o[14] = 0; o[15] = 1;
      return o;
    },
    copy(o, a) { for (let i = 0; i < 16; i++) o[i] = a[i]; return o; },

    /* Rotate a colour about the (1,1,1) axis by a radians. This is the same
       Rodrigues rotation as hue() in FS_SCENE, kept here as well because the
       fallback renderer applies it on the CPU: that path has no room for a hue
       uniform without giving up on the "plain GLSL only" rule that lets it run
       on drivers which rejected the full shader set. Both must agree, so the
       formula is duplicated deliberately and controltest checks they do. */
    HUE_K: 0.57735,
    hueRotate(o, c, a) {
      const k = M4.HUE_K, ca = Math.cos(a), sa = Math.sin(a), ic = 1 - ca;
      const x = c[0], y = c[1], z = c[2];
      const kd = k * (x + y + z);
      /* cross(k, c) with k = (k,k,k) */
      const cx = k * (y - z), cy = k * (z - x), cz = k * (x - y);
      o[0] = x * ca + cx * sa + k * kd * ic;
      o[1] = y * ca + cy * sa + k * kd * ic;
      o[2] = z * ca + cz * sa + k * kd * ic;
      return o;
    },

    /* o = a * b  (apply b first, then a) */
    mul(o, a, b) {
      const a00 = a[0], a01 = a[1], a02 = a[2], a03 = a[3];
      const a10 = a[4], a11 = a[5], a12 = a[6], a13 = a[7];
      const a20 = a[8], a21 = a[9], a22 = a[10], a23 = a[11];
      const a30 = a[12], a31 = a[13], a32 = a[14], a33 = a[15];
      for (let i = 0; i < 4; i++) {
        const b0 = b[i * 4], b1 = b[i * 4 + 1], b2 = b[i * 4 + 2], b3 = b[i * 4 + 3];
        o[i * 4] = b0 * a00 + b1 * a10 + b2 * a20 + b3 * a30;
        o[i * 4 + 1] = b0 * a01 + b1 * a11 + b2 * a21 + b3 * a31;
        o[i * 4 + 2] = b0 * a02 + b1 * a12 + b2 * a22 + b3 * a32;
        o[i * 4 + 3] = b0 * a03 + b1 * a13 + b2 * a23 + b3 * a33;
      }
      return o;
    },

    perspective(o, fovy, aspect, near, far) {
      const f = 1 / Math.tan(fovy / 2);
      o[0] = f / aspect; o[1] = 0; o[2] = 0; o[3] = 0;
      o[4] = 0; o[5] = f; o[6] = 0; o[7] = 0;
      o[8] = 0; o[9] = 0; o[10] = (far + near) / (near - far); o[11] = -1;
      o[12] = 0; o[13] = 0; o[14] = (2 * far * near) / (near - far); o[15] = 0;
      return o;
    },

    ortho(o, l, r, b, t, n, f) {
      const lr = 1 / (l - r), bt = 1 / (b - t), nf = 1 / (n - f);
      o[0] = -2 * lr; o[1] = 0; o[2] = 0; o[3] = 0;
      o[4] = 0; o[5] = -2 * bt; o[6] = 0; o[7] = 0;
      o[8] = 0; o[9] = 0; o[10] = 2 * nf; o[11] = 0;
      o[12] = (l + r) * lr; o[13] = (t + b) * bt; o[14] = (f + n) * nf; o[15] = 1;
      return o;
    },

    lookAt(o, eye, center, up) {
      let z0 = eye[0] - center[0], z1 = eye[1] - center[1], z2 = eye[2] - center[2];
      let len = Math.hypot(z0, z1, z2);
      if (len < 1e-8) { z0 = 0; z1 = 0; z2 = 1; len = 1; }
      z0 /= len; z1 /= len; z2 /= len;
      let x0 = up[1] * z2 - up[2] * z1;
      let x1 = up[2] * z0 - up[0] * z2;
      let x2 = up[0] * z1 - up[1] * z0;
      len = Math.hypot(x0, x1, x2);
      if (len < 1e-8) { x0 = 1; x1 = 0; x2 = 0; } else { x0 /= len; x1 /= len; x2 /= len; }
      const y0 = z1 * x2 - z2 * x1, y1 = z2 * x0 - z0 * x2, y2 = z0 * x1 - z1 * x0;
      o[0] = x0; o[1] = y0; o[2] = z0; o[3] = 0;
      o[4] = x1; o[5] = y1; o[6] = z1; o[7] = 0;
      o[8] = x2; o[9] = y2; o[10] = z2; o[11] = 0;
      o[12] = -(x0 * eye[0] + x1 * eye[1] + x2 * eye[2]);
      o[13] = -(y0 * eye[0] + y1 * eye[1] + y2 * eye[2]);
      o[14] = -(z0 * eye[0] + z1 * eye[1] + z2 * eye[2]);
      o[15] = 1;
      return o;
    },

    /* compose: T * Rx * Ry * Rz * S  (rot applied right-to-left on the vertex) */
    compose(o, px, py, pz, rx, ry, rz, s) {
      const sx = s ? s[0] : 1, sy = s ? s[1] : 1, sz = s ? s[2] : 1;
      const cx = Math.cos(rx), sxr = Math.sin(rx);
      const cy = Math.cos(ry), syr = Math.sin(ry);
      const cz = Math.cos(rz), szr = Math.sin(rz);
      // R = Rx*Ry*Rz
      const r00 = cy * cz;
      const r01 = -cy * szr;
      const r02 = syr;
      const r10 = sxr * syr * cz + cx * szr;
      const r11 = -sxr * syr * szr + cx * cz;
      const r12 = -sxr * cy;
      const r20 = -cx * syr * cz + sxr * szr;
      const r21 = cx * syr * szr + sxr * cz;
      const r22 = cx * cy;
      o[0] = r00 * sx; o[1] = r10 * sx; o[2] = r20 * sx; o[3] = 0;
      o[4] = r01 * sy; o[5] = r11 * sy; o[6] = r21 * sy; o[7] = 0;
      o[8] = r02 * sz; o[9] = r12 * sz; o[10] = r22 * sz; o[11] = 0;
      o[12] = px; o[13] = py; o[14] = pz; o[15] = 1;
      return o;
    },

    translate(o, x, y, z) { M4.identity(o); o[12] = x; o[13] = y; o[14] = z; return o; },

    /* project a world-space point by m -> [x,y,z,w] in out(4) */
    xformPoint(out, m, x, y, z) {
      out[0] = m[0] * x + m[4] * y + m[8] * z + m[12];
      out[1] = m[1] * x + m[5] * y + m[9] * z + m[13];
      out[2] = m[2] * x + m[6] * y + m[10] * z + m[14];
      out[3] = m[3] * x + m[7] * y + m[11] * z + m[15];
      return out;
    },

    invert(o, a) {
      const a00 = a[0], a01 = a[1], a02 = a[2], a03 = a[3];
      const a10 = a[4], a11 = a[5], a12 = a[6], a13 = a[7];
      const a20 = a[8], a21 = a[9], a22 = a[10], a23 = a[11];
      const a30 = a[12], a31 = a[13], a32 = a[14], a33 = a[15];
      const b00 = a00 * a11 - a01 * a10, b01 = a00 * a12 - a02 * a10;
      const b02 = a00 * a13 - a03 * a10, b03 = a01 * a12 - a02 * a11;
      const b04 = a01 * a13 - a03 * a11, b05 = a02 * a13 - a03 * a12;
      const b06 = a20 * a31 - a21 * a30, b07 = a20 * a32 - a22 * a30;
      const b08 = a20 * a33 - a23 * a30, b09 = a21 * a32 - a22 * a31;
      const b10 = a21 * a33 - a23 * a31, b11 = a22 * a33 - a23 * a32;
      let det = b00 * b11 - b01 * b10 + b02 * b09 + b03 * b08 - b04 * b07 + b05 * b06;
      if (!det) return null;
      det = 1 / det;
      o[0] = (a11 * b11 - a12 * b10 + a13 * b09) * det;
      o[1] = (a02 * b10 - a01 * b11 - a03 * b09) * det;
      o[2] = (a31 * b05 - a32 * b04 + a33 * b03) * det;
      o[3] = (a22 * b04 - a21 * b05 - a23 * b03) * det;
      o[4] = (a12 * b08 - a10 * b11 - a13 * b07) * det;
      o[5] = (a00 * b11 - a02 * b08 + a03 * b07) * det;
      o[6] = (a32 * b02 - a30 * b05 - a33 * b01) * det;
      o[7] = (a20 * b05 - a22 * b02 + a23 * b01) * det;
      o[8] = (a10 * b10 - a11 * b08 + a13 * b06) * det;
      o[9] = (a01 * b08 - a00 * b10 - a03 * b06) * det;
      o[10] = (a30 * b04 - a31 * b02 + a33 * b00) * det;
      o[11] = (a21 * b02 - a20 * b04 - a23 * b00) * det;
      o[12] = (a11 * b07 - a10 * b09 - a12 * b06) * det;
      o[13] = (a00 * b09 - a01 * b07 + a02 * b06) * det;
      o[14] = (a31 * b01 - a30 * b03 - a32 * b00) * det;
      o[15] = (a20 * b03 - a21 * b01 + a22 * b00) * det;
      return o;
    },

    /* normal matrix = transpose(inverse(upper 3x3)) into Float32Array(9) */
    normalMat3(o9, m) {
      const a = m[0], b = m[1], c = m[2];
      const d = m[4], e = m[5], f = m[6];
      const g = m[8], h = m[9], i = m[10];
      const A = e * i - f * h, B = f * g - d * i, C = d * h - e * g;
      let det = a * A + b * B + c * C;
      if (!det) { o9[0] = 1; o9[1] = 0; o9[2] = 0; o9[3] = 0; o9[4] = 1; o9[5] = 0; o9[6] = 0; o9[7] = 0; o9[8] = 1; return o9; }
      det = 1 / det;
      o9[0] = A * det; o9[1] = B * det; o9[2] = C * det;
      o9[3] = (c * h - b * i) * det; o9[4] = (a * i - c * g) * det; o9[5] = (b * g - a * h) * det;
      o9[6] = (b * f - c * e) * det; o9[7] = (c * d - a * f) * det; o9[8] = (a * e - b * d) * det;
      return o9;
    },

    /* project the model matrix flat onto the y=0 plane (planar shadow) */
    flattenY(o, m) {
      o[0] = m[0]; o[1] = 0; o[2] = m[2]; o[3] = 0;
      o[4] = m[4]; o[5] = 0; o[6] = m[6]; o[7] = 0;
      o[8] = m[8]; o[9] = 0; o[10] = m[10]; o[11] = 0;
      o[12] = m[12]; o[13] = 0; o[14] = m[14]; o[15] = 1;
      return o;
    }
  };

  const V3 = {
    sub(o, a, b) { o[0] = a[0] - b[0]; o[1] = a[1] - b[1]; o[2] = a[2] - b[2]; return o; },
    add(o, a, b) { o[0] = a[0] + b[0]; o[1] = a[1] + b[1]; o[2] = a[2] + b[2]; return o; },
    scale(o, a, s) { o[0] = a[0] * s; o[1] = a[1] * s; o[2] = a[2] * s; return o; },
    dot(a, b) { return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]; },
    cross(o, a, b) {
      const x = a[1] * b[2] - a[2] * b[1];
      const y = a[2] * b[0] - a[0] * b[2];
      const z = a[0] * b[1] - a[1] * b[0];
      o[0] = x; o[1] = y; o[2] = z; return o;
    },
    len(a) { return Math.hypot(a[0], a[1], a[2]); },
    norm(o, a) {
      const l = Math.hypot(a[0], a[1], a[2]) || 1;
      o[0] = a[0] / l; o[1] = a[1] / l; o[2] = a[2] / l; return o;
    }
  };

  const clamp = (v, a, b) => (v < a ? a : v > b ? b : v);
  const lerp = (a, b, t) => a + (b - a) * t;
  const smoothstep = (e0, e1, x) => {
    const t = clamp((x - e0) / (e1 - e0), 0, 1);
    return t * t * (3 - 2 * t);
  };
  /* shortest-path angular lerp */
  const mixAngle = (a, b, t) => {
    let d = ((b - a + Math.PI) % (Math.PI * 2)) - Math.PI;
    if (d < -Math.PI) d += Math.PI * 2;
    return a + d * t;
  };
  const fract = (x) => x - Math.floor(x);
  const TAU = Math.PI * 2;
  const DEG = Math.PI / 180;

  global.M4 = M4;
  global.V3 = V3;
  global.clamp = clamp;
  global.lerp = lerp;
  global.smoothstep = smoothstep;
  global.mixAngle = mixAngle;
  global.fract = fract;
  global.TAU = TAU;
  global.DEG = DEG;
})(window);
