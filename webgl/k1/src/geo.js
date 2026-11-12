/* geo.js - flat-shaded low-poly geometry generators + mesh merging */
(function (global) {
  'use strict';

  /* every generator returns {pos:[], nrm:[], col:[], idx:[]}
     col = [r,g,b,emissive] per vertex */

  function pushFlat(G, verts, indices, col) {
    /* verts: [[x,y,z],...] triangle soup, each vertex already in world-of-part
       space, three per triangle and never shared between triangles (every
       generator passes sequential indices), so true flat shading costs nothing:
       one normal per triangle, given to its three vertices.

       This used to sum the face normals of the *whole part* and use that one
       vector for every vertex, which is wrong twice over. For a symmetric box
       the six faces cancel to exactly zero, so the whole box got a zero
       normal - the pelvis, the hands and the feet came out entirely zero. For a
       tapered part the sum did not cancel, so every face got the same arbitrary
       non-unit normal, which is why the arms had a mean normal length of 0.73
       rather than 1.

       A zero normal is not merely bad shading: normalize(vec3(0)) is undefined
       in GLSL, a driver that returns NaN turns every such fragment black, and
       a black body reads on screen as a hole in the robot. */
    const base = G.pos.length / 3;
    // part centroid, used only to rescue a degenerate triangle
    let cx = 0, cy = 0, cz = 0;
    for (let i = 0; i < verts.length; i++) { cx += verts[i][0]; cy += verts[i][1]; cz += verts[i][2]; }
    const n = verts.length || 1;
    cx /= n; cy /= n; cz /= n;

    for (let i = 0; i < verts.length; i += 3) {
      const a = verts[i], b = verts[i + 1], c = verts[i + 2];
      const ux = b[0] - a[0], uy = b[1] - a[1], uz = b[2] - a[2];
      const vx = c[0] - a[0], vy = c[1] - a[1], vz = c[2] - a[2];
      let nx = uy * vz - uz * vy;
      let ny = uz * vx - ux * vz;
      let nz = ux * vy - uy * vx;
      let l = Math.hypot(nx, ny, nz);
      if (l < 1e-12) {
        /* a collapsed triangle - a sphere pole, or a cap triangle at zero
           radius. Point outwards from the part rather than leave it zero. */
        nx = a[0] - cx; ny = a[1] - cy; nz = a[2] - cz;
        l = Math.hypot(nx, ny, nz);
        if (l < 1e-12) { nx = 0; ny = 1; nz = 0; l = 1; }
      }
      nx /= l; ny /= l; nz /= l;
      const cr = col[0], cg = col[1], cb = col[2], ce = col.length > 3 ? col[3] : 0;
      for (let k = 0; k < 3; k++) {
        const v = verts[i + k];
        G.pos.push(v[0], v[1], v[2]);
        G.nrm.push(nx, ny, nz);
        G.col.push(cr, cg, cb, ce);
      }
    }
    for (let i = 0; i < indices.length; i++) G.idx.push(base + indices[i]);
  }

  /* box centred on origin */
  function box(w, h, d, col) {
    const G = { pos: [], nrm: [], col: [], idx: [] };
    const x = w / 2, y = h / 2, z = d / 2;
    const F = [
      // +Z, -Z, +X, -X, +Y, -Y
      [[-x, -y, z], [x, -y, z], [x, y, z], [-x, y, z]],
      [[x, -y, -z], [-x, -y, -z], [-x, y, -z], [x, y, -z]],
      [[x, -y, z], [x, -y, -z], [x, y, -z], [x, y, z]],
      [[-x, -y, -z], [-x, -y, z], [-x, y, z], [-x, y, -z]],
      [[-x, y, z], [x, y, z], [x, y, -z], [-x, y, -z]],
      [[-x, -y, -z], [x, -y, -z], [x, -y, z], [-x, -y, z]]
    ];
    const all = [];
    for (const f of F) all.push(f[0], f[1], f[2], f[0], f[2], f[3]);
    pushFlat(G, all, all.map((_, i) => i), col);
    return G;
  }

  /* box from y=0 down to y=-h (limb friendly), optionally tapered */
  function limb(wTop, dTop, wBot, dBot, h, col) {
    const G = { pos: [], nrm: [], col: [], idx: [] };
    const a = wTop / 2, b = dTop / 2, c = wBot / 2, d = dBot / 2;
    const t0 = [a, 0, b], t1 = [-a, 0, b], t2 = [-a, 0, -b], t3 = [a, 0, -b];
    const b0 = [c, -h, d], b1 = [-c, -h, d], b2 = [-c, -h, -d], b3 = [c, -h, -d];
    const quads = [
      [t0, t3, b3, b0],   // +Z
      [t1, t0, b0, b1],   // +X
      [t2, t1, b1, b2],   // -Z
      [t3, t2, b2, b3],   // -X
      [t0, t1, t2, t3],   // top
      [b1, b0, b3, b2]    // bottom
    ];
    const all = [];
    for (const q of quads) all.push(q[0], q[1], q[2], q[0], q[2], q[3]);
    pushFlat(G, all, all.map((_, i) => i), col);
    return G;
  }

  /* cylinder along -Y from y=0 to y=-h, seg segments (flat shaded) */
  function cyl(rTop, rBot, h, seg, col) {
    const G = { pos: [], nrm: [], col: [], idx: [] };
    const all = [];
    for (let i = 0; i < seg; i++) {
      const a0 = (i / seg) * Math.PI * 2, a1 = ((i + 1) / seg) * Math.PI * 2;
      const p0 = [Math.cos(a0) * rTop, 0, Math.sin(a0) * rTop];
      const p1 = [Math.cos(a1) * rTop, 0, Math.sin(a1) * rTop];
      const p2 = [Math.cos(a1) * rBot, -h, Math.sin(a1) * rBot];
      const p3 = [Math.cos(a0) * rBot, -h, Math.sin(a0) * rBot];
      all.push(p0, p1, p2, p0, p2, p3);
    }
    // caps
    for (let i = 1; i < seg - 1; i++) {
      const a0 = 0, a1 = (i / seg) * Math.PI * 2, a2 = ((i + 1) / seg) * Math.PI * 2;
      all.push([0, 0, 0], [Math.cos(a1) * rTop, 0, Math.sin(a1) * rTop], [Math.cos(a2) * rTop, 0, Math.sin(a2) * rTop]);
      all.push([0, -h, 0], [Math.cos(a2) * rBot, -h, Math.sin(a2) * rBot], [Math.cos(a1) * rBot, -h, Math.sin(a1) * rBot]);
    }
    pushFlat(G, all, all.map((_, i) => i), col);
    return G;
  }

  /* low poly sphere */
  function sphere(r, seg, rings, col) {
    const G = { pos: [], nrm: [], col: [], idx: [] };
    const P = (i, j) => {
      const u = (i / seg) * Math.PI * 2, v = (j / rings) * Math.PI;
      return [Math.cos(u) * Math.sin(v) * r, Math.cos(v) * r, Math.sin(u) * Math.sin(v) * r];
    };
    const all = [];
    for (let j = 0; j < rings; j++) {
      for (let i = 0; i < seg; i++) {
        const a = P(i, j), b = P(i + 1, j), c = P(i + 1, j + 1), d = P(i, j + 1);
        all.push(a, b, c, a, c, d);
      }
    }
    pushFlat(G, all, all.map((_, i) => i), col);
    return G;
  }

  /* quad on the XZ plane at y=0, centred, size s (used for floor) */
  function plane(s, col) {
    const G = { pos: [], nrm: [], col: [], idx: [] };
    const h = s / 2;
    const v = [[-h, 0, -h], [h, 0, -h], [h, 0, h], [-h, 0, h]];
    const all = [v[0], v[1], v[2], v[0], v[2], v[3]];
    pushFlat(G, all, all.map((_, i) => i), col || [1, 1, 1, 0]);
    return G;
  }

  /* apply translate/rotate/scale to a copy of a geometry */
  function xform(G, o) {
    const t = o.t || [0, 0, 0];
    const r = o.r || [0, 0, 0];
    const s = o.s || 1;
    const sx = Array.isArray(s) ? s[0] : s, sy = Array.isArray(s) ? s[1] : s, sz = Array.isArray(s) ? s[2] : s;
    const cx = Math.cos(r[0]), Rx = Math.sin(r[0]);
    const cy = Math.cos(r[1]), Ry = Math.sin(r[1]);
    const cz = Math.cos(r[2]), Rz = Math.sin(r[2]);
    // R = Rx*Ry*Rz
    const m00 = cy * cz, m01 = -cy * Rz, m02 = Ry;
    const m10 = Rx * Ry * cz + cx * Rz, m11 = -Rx * Ry * Rz + cx * cz, m12 = -Rx * cy;
    const m20 = -cx * Ry * cz + Rx * Rz, m21 = cx * Ry * Rz + Rx * cz, m22 = cx * cy;
    const out = { pos: new Array(G.pos.length), nrm: new Array(G.nrm.length), col: G.col.slice(), idx: G.idx.slice() };
    for (let i = 0; i < G.pos.length; i += 3) {
      const x = G.pos[i] * sx, y = G.pos[i + 1] * sy, z = G.pos[i + 2] * sz;
      out.pos[i] = m00 * x + m01 * y + m02 * z + t[0];
      out.pos[i + 1] = m10 * x + m11 * y + m12 * z + t[1];
      out.pos[i + 2] = m20 * x + m21 * y + m22 * z + t[2];
      const nx = G.nrm[i] / sx, ny = G.nrm[i + 1] / sy, nz = G.nrm[i + 2] / sz;
      let ax = m00 * nx + m01 * ny + m02 * nz;
      let ay = m10 * nx + m11 * ny + m12 * nz;
      let az = m20 * nx + m21 * ny + m22 * nz;
      const l = Math.hypot(ax, ay, az);
      // no `|| 1` here: it would silently pass a zero normal through to the
      // GPU, and a zero normal renders black once normalize() sees it
      if (l < 1e-12) { ax = 0; ay = 1; az = 0; }
      else { ax /= l; ay /= l; az /= l; }
      out.nrm[i] = ax; out.nrm[i + 1] = ay; out.nrm[i + 2] = az;
    }
    return out;
  }

  function tint(G, col) {
    const out = { pos: G.pos, nrm: G.nrm, idx: G.idx, col: new Array(G.col.length) };
    for (let i = 0; i < G.col.length; i += 4) {
      out.col[i] = col[0]; out.col[i + 1] = col[1]; out.col[i + 2] = col[2];
      out.col[i + 3] = col.length > 3 ? col[3] : 0;
    }
    return out;
  }

  function merge(list) {
    const out = { pos: [], nrm: [], col: [], idx: [] };
    for (const G of list) {
      const base = out.pos.length / 3;
      for (let i = 0; i < G.pos.length; i++) out.pos.push(G.pos[i]);
      for (let i = 0; i < G.nrm.length; i++) out.nrm.push(G.nrm[i]);
      for (let i = 0; i < G.col.length; i++) out.col.push(G.col[i]);
      for (let i = 0; i < G.idx.length; i++) out.idx.push(base + G.idx[i]);
    }
    return out;
  }

  function toBuffers(G) {
    return {
      pos: new Float32Array(G.pos),
      nrm: new Float32Array(G.nrm),
      col: new Float32Array(G.col),
      idx: (G.pos.length / 3) > 65535 ? new Uint32Array(G.idx) : new Uint16Array(G.idx)
    };
  }

  /* unique edges of a triangle soup, for a GL_LINES pass */
  function wireIdx(pos, idx) {
    const key = (i) => (Math.round(pos[i * 3] * 2048) + ',' + Math.round(pos[i * 3 + 1] * 2048) + ',' + Math.round(pos[i * 3 + 2] * 2048));
    const remap = new Map();
    const k2i = new Map();
    for (let i = 0; i < idx.length; i++) {
      const k = key(idx[i]);
      let v = k2i.get(k);
      if (v === undefined) { v = remap.size; k2i.set(k, v); }
      remap.set(idx[i], v);
    }
    const seen = new Set();
    const out = [];
    const edge = (a, b) => {
      if (a === b) return;
      const k = a < b ? a * 100000 + b : b * 100000 + a;
      if (seen.has(k)) return;
      seen.add(k);
      out.push(a, b);
    };
    for (let i = 0; i < idx.length; i += 3) {
      const a = remap.get(idx[i]), b = remap.get(idx[i + 1]), c = remap.get(idx[i + 2]);
      edge(a, b); edge(b, c); edge(c, a);
    }
    return new Uint16Array(out);
  }

  global.Geo = { box, limb, cyl, sphere, plane, xform, tint, merge, toBuffers, wireIdx };
})(window);
