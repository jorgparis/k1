/* shaders.js - GLSL ES 1.00 sources */
(function (global) {
  'use strict';

  /* ------------------------------------------------------------ shared */
  const VS_SCENE = `
attribute vec3 aPos;
attribute vec3 aNormal;
attribute vec4 aColor;
uniform mat4 uVP;
uniform mat4 uModel;
uniform mat3 uNrm;
varying vec3 vN;
varying vec3 vW;
varying vec4 vC;
void main(){
  vec4 w = uModel * vec4(aPos, 1.0);
  vW = w.xyz;
  vN = uNrm * aNormal;
  vC = aColor;
  gl_Position = uVP * w;
}`;

  const FS_SCENE = `
precision mediump float;
varying vec3 vN;
varying vec3 vW;
varying vec4 vC;
uniform vec3 uCam;
uniform vec3 uLight;
uniform vec3 uFogCol;
uniform float uFog;
uniform float uHue;
uniform float uMode;   // 0 solid, 1 normal colour, 2 mono light

vec3 hue(vec3 c, float a){
  const vec3 k = vec3(0.57735);
  float ca = cos(a);
  return c * ca + cross(k, c) * sin(a) + k * dot(k, c) * (1.0 - ca);
}

void main(){
  /* normalize(vec3(0)) is undefined in GLSL ES 1.00, and a driver that
     returns NaN for it turns the whole fragment black - which reads on screen
     as a hole in the robot. geo.js guarantees a non-zero normal per face, but
     a single unguarded normalize anywhere is a black-body waiting to happen. */
  float nl = length(vN);
  vec3 n = nl > 0.0 ? vN / nl : vec3(0.0, 1.0, 0.0);
  vec3 v = normalize(uCam - vW);
  vec3 l = normalize(uLight);
  float key = max(dot(n, l), 0.0);
  float fill = max(dot(n, normalize(vec3(-0.55, 0.30, -0.45))), 0.0);
  float sky = 0.5 + 0.5 * n.y;
  vec3 c = vC.rgb * (0.20 + 0.70 * key + 0.20 * fill);
  c += vC.rgb * sky * 0.22;
  vec3 h = normalize(l + v);
  float spec = pow(max(dot(n, h), 0.0), 22.0) * 0.22;
  c += vec3(spec);
  float rim = pow(1.0 - max(dot(n, v), 0.0), 3.0);
  c += vec3(0.20, 0.40, 0.70) * rim * 0.30;
  c = mix(c, vC.rgb * 1.45, clamp(vC.a, 0.0, 1.0));

  if (uMode > 1.5) {
    c = vec3(0.15 + 0.85 * key);
  } else if (uMode > 0.5) {
    vec3 nc = n * 0.5 + 0.5;
    nc = floor(nc * 5.0 + 0.5) / 5.0;
    c = mix(nc, nc * (0.35 + 0.9 * key), 0.5);
  }
  if (uHue != 0.0) c = hue(c, uHue);

  float f = clamp((length(uCam - vW) - 5.0) / 45.0, 0.0, 1.0) * uFog;
  c = mix(c, uFogCol, f);
  gl_FragColor = vec4(c, 1.0);
}`;

  /* shadow silhouette: same model matrix, flattened by the CPU */
  const FS_SHADOW = `
precision mediump float;
varying vec3 vW;
uniform vec3 uCam;
uniform float uFog;
uniform vec3 uFogCol;
uniform float uDark;
void main(){
  float d = length(uCam - vW);
  float a = uDark * (1.0 - clamp((d - 5.0) / 25.0, 0.0, 1.0));
  gl_FragColor = vec4(mix(uFogCol, vec3(0.0), 0.85), a);
}`;

  /* ---------------------------------------------------------------- floor */
  const VS_FLOOR = `
attribute vec3 aPos;
uniform mat4 uVP;
uniform mat4 uModel;
varying vec3 vW;
void main(){
  vec4 w = uModel * vec4(aPos, 1.0);
  vW = w.xyz;
  gl_Position = uVP * w;
}`;

  const FS_FLOOR = `
precision mediump float;
varying vec3 vW;
uniform vec3 uCam;
uniform vec2 uFwd;      // forward in XZ (unit)
uniform float uOffset;  // travel along forward
uniform float uFog;
uniform vec3 uFogCol;
uniform vec3 uBase;
uniform vec3 uLine;
uniform float uTime;

float lineMask(float x, float w){
  float d = abs(fract(x + 0.5) - 0.5);
  return 1.0 - smoothstep(0.0, w, d);
}
void main(){
  float f = dot(vW.xz, uFwd) + uOffset;
  float r = dot(vW.xz, vec2(-uFwd.y, uFwd.x));
  float dist = length(uCam - vW);
  float w = clamp(dist * 0.0022, 0.004, 0.12);

  float minor = max(lineMask(f, w * 1.1), lineMask(r, w * 1.1));
  float major = max(lineMask(f * 0.25, w * 1.6), lineMask(r * 0.25, w * 1.6));

  // 4x4 checker for motion cue
  float chk = mod(floor(f * 0.25) + floor(r * 0.25), 2.0);

  vec3 c = uBase * mix(1.0, 0.78, chk);
  c = mix(c, uLine, minor * 0.30);
  c = mix(c, uLine, major * 0.70);

  // glow pulse travelling with the k1
  float pulse = smoothstep(0.0, 1.0, 1.0 - abs(fract(f * 0.0625) - 0.5) * 2.0);
  c += uLine * pulse * 0.05;

  float fog = clamp((dist - 6.0) / 60.0, 0.0, 1.0) * uFog;
  c = mix(c, uFogCol, fog);
  gl_FragColor = vec4(c, 1.0);
}`;

  /* ------------------------------------------------------------------ sky */
  const VS_SKY = `
attribute vec2 aPos;
uniform mat4 uInvVP;
uniform vec3 uCam;
varying vec3 vRay;
void main(){
  vec4 p = uInvVP * vec4(aPos, 1.0, 1.0);
  vRay = p.xyz / p.w - uCam;
  gl_Position = vec4(aPos, 0.999, 1.0);
}`;

  const FS_SKY = `
precision mediump float;
varying vec3 vRay;
uniform float uTime;
uniform vec3 uZenith;
uniform vec3 uHorizon;
uniform vec3 uSunCol;
uniform float uBands;

float hash21(vec2 p){
  p = fract(p * vec2(123.34, 456.21));
  // spelled out rather than "p += dot(p, p + 45.32)": binary + allows a scalar
  // operand, but compound assignment with one is not guaranteed by GLSL ES
  // 1.00, and a driver that disagrees fails the whole program.
  p = p + vec2(dot(p, p + 45.32));
  return fract(p.x * p.y);
}

void main(){
  vec3 r = normalize(vRay);
  float y = r.y;

  // retro banded gradient
  float g = clamp(y * 1.15, 0.0, 1.0);
  float gb = floor(g * uBands) / uBands;
  vec3 c = mix(uHorizon, uZenith, pow(clamp(gb, 0.0, 1.0), 0.65));

  // stars
  if (y > 0.02) {
    vec2 sc = vec2(atan(r.z, r.x) * 2.2, asin(clamp(y, -1.0, 1.0)) * 3.0);
    vec2 cell = floor(sc * 42.0);
    float h = hash21(cell);
    if (h > 0.972) {
      float d = length(fract(sc * 42.0) - 0.5);
      float tw = 0.6 + 0.4 * sin(uTime * 3.0 + h * 40.0);
      // 1.0 - smoothstep, not smoothstep(0.30, 0.0, d): the spec leaves
      // smoothstep undefined when edge0 >= edge1
      c += vec3(0.9, 0.95, 1.0) * (1.0 - smoothstep(0.0, 0.30, d)) * tw * clamp(y * 3.0, 0.0, 1.0);
    }
  }

  // sun with horizontal slits
  vec3 sunDir = normalize(vec3(cos(uTime * 0.05) * 0.25, 0.075, -1.0));
  float sd = dot(r, sunDir);
  float disc = smoothstep(0.9982, 0.9990, sd);
  float band = fract(y * 46.0);
  float t = clamp((0.075 - y) * 4.0, 0.0, 1.0);
  float keep = step(band, mix(1.01, 0.30, t));
  float sun = disc * mix(1.0, keep, step(y, 0.08));
  float glow = pow(max(sd, 0.0), 320.0) * 0.9 + pow(max(sd, 0.0), 26.0) * 0.30;
  c += uSunCol * glow;
  c = mix(c, uSunCol * 1.15, sun * 0.92);

  // skyline silhouette around the horizon
  float az = atan(r.z, r.x);
  float bcell = floor(az * 11.0);
  float bh = 0.006 + hash21(vec2(bcell, 7.0)) * 0.040;
  float bh2 = 0.004 + hash21(vec2(floor(az * 34.0), 3.0)) * 0.016;
  float sky_h = max(bh, y < 0.02 ? bh2 : 0.0);
  if (y < sky_h) {
    vec3 sil = mix(vec3(0.03, 0.03, 0.07), uHorizon * 0.35, 0.4);
    // windows
    vec2 wcell = floor(vec2(az * 190.0, y * 420.0));
    float wh = hash21(wcell + 3.0);
    if (wh > 0.86) sil += vec3(1.0, 0.75, 0.25) * 0.55;
    c = mix(c, sil, 0.94);
  }
  // haze
  c = mix(c, uHorizon, clamp(1.0 - y * 9.0, 0.0, 1.0) * 0.55);

  gl_FragColor = vec4(c, 1.0);
}`;

  /* -------------------------------------------------------- impact rings */
  const VS_RING = `
attribute vec3 aPos;      // unit quad in xz
attribute vec2 aLife;     // age01, seed
uniform mat4 uVP;
uniform vec3 uOrigin;
uniform float uRadius;
varying vec2 vLocal;
varying vec2 vLife;
void main(){
  vLocal = aPos.xy;
  vLife = aLife;
  vec3 w = vec3(uOrigin.x + aPos.x * uRadius, 0.012, uOrigin.z + aPos.y * uRadius);
  gl_Position = uVP * vec4(w, 1.0);
}`;

  const FS_RING = `
precision mediump float;
varying vec2 vLocal;
varying vec2 vLife;
uniform vec3 uCol;
void main(){
  float r = length(vLocal);
  float age = vLife.x;
  float w = 0.05 + age * 0.16;
  float rad = 0.18 + age * 0.82;
  float band = 1.0 - smoothstep(0.0, w, abs(r - rad));
  float a = band * (1.0 - age) * (1.0 - age) * 0.9;
  if (a < 0.01) discard;
  gl_FragColor = vec4(uCol, a);
}`;

  /* ----------------------------------------------------------------- post */
  const VS_POST = `
attribute vec2 aPos;
varying vec2 vUv;
void main(){
  vUv = aPos * 0.5 + 0.5;
  gl_Position = vec4(aPos, 0.0, 1.0);
}`;

  const FS_POST = `
precision mediump float;
varying vec2 vUv;
uniform sampler2D uScene;
uniform sampler2D uHud;
uniform vec2 uRes;
uniform float uTime;
uniform float uLevels;
uniform float uDither;
uniform float uScan;
uniform float uMask;
uniform float uAberr;
uniform float uFlash;
uniform float uHudOn;

float bayer2(vec2 a){ a = floor(a); return fract(a.x / 2.0 + a.y * a.y * 0.75); }
#define bayer4(a) (bayer2(0.5 * (a)) * 0.25 + bayer2(a))
#define bayer8(a) (bayer2(0.25 * (a)) * 0.0625 + bayer2(0.5 * (a)) * 0.25 + bayer2(a))

void main(){
  vec2 uv = vUv;
  vec2 px = uv * uRes;
  vec2 fromC = uv - 0.5;

  // barrel-ish chromatic aberration towards the edges
  float ab = uAberr * dot(fromC, fromC);
  vec3 c;
  c.r = texture2D(uScene, uv - fromC * ab * 1.6).r;
  c.g = texture2D(uScene, uv).g;
  c.b = texture2D(uScene, uv + fromC * ab * 1.6).b;

  // HUD overlay (pixel perfect, already low res) - flipped: canvas row0 is v=1
  if (uHudOn > 0.5) {
    vec4 h = texture2D(uHud, vec2(uv.x, 1.0 - uv.y));
    c = mix(c, h.rgb, h.a);
  }

  // scanlines + aperture grille
  float sl = sin(px.y * 3.14159);
  c *= 1.0 - uScan * (0.5 - 0.5 * sl);
  float mx = mod(px.x, 3.0);
  vec3 mask = vec3(
    1.0 - uMask * smoothstep(0.6, 1.6, abs(mx - 0.5)),
    1.0 - uMask * smoothstep(0.6, 1.6, abs(mx - 1.5)),
    1.0 - uMask * smoothstep(0.6, 1.6, abs(mx - 2.5)));
  c *= mask;

  // flash / beat pulse
  c += uFlash * vec3(0.35, 0.45, 0.6);

  // vignette
  c *= 1.0 - dot(fromC, fromC) * 0.55;

  // ordered dither + palette quantisation
  float d = bayer8(px) - 0.5;
  vec3 q = floor(c * uLevels + 0.5 + d * uDither) / uLevels;

  gl_FragColor = vec4(max(q, 0.0), 1.0);
}`;

  /* ------------------------------------------------- minimal fallback set */
  /* Some drivers refuse the full shader set above. These four are about as
     plain as GLSL ES 1.00 gets, so if the real thing will not compile the demo
     still renders. Degraded, but visible, which beats a black screen.

     A keypress that changes nothing is indistinguishable from a broken demo,
     so this pair has to honour the controls too: W and C work on the CPU side
     (wireframe index buffer, hue rotation), and N needs a real per-face normal,
     hence the second varying. Everything is still one attribute, two uniforms
     and arithmetic - nothing here that FS_SCENE does not also do. */
  const VS_MIN = `
attribute vec3 aPos;
attribute vec3 aNormal;
uniform mat4 uVP;
uniform mat4 uModel;
uniform vec3 uColor;
varying vec3 vC;
varying vec3 vN;
void main(){
  vC = uColor;
  vN = mat3(uModel) * aNormal;
  gl_Position = uVP * uModel * vec4(aPos, 1.0);
}`;

  const FS_MIN = `
precision mediump float;
uniform vec3 uLight;
uniform float uMode;
varying vec3 vC;
varying vec3 vN;
void main(){
  // guarded rather than a bare normalize: see the note in FS_SCENE
  float nl = length(vN);
  vec3 n = nl > 0.0 ? vN / nl : vec3(0.0, 1.0, 0.0);
  float key = max(dot(n, normalize(uLight)), 0.0);
  vec3 c = vC;
  if (uMode > 1.5) {
    c = vec3(0.15 + 0.85 * key);
  } else if (uMode > 0.5) {
    vec3 nc = n * 0.5 + 0.5;
    nc = floor(nc * 5.0 + 0.5) / 5.0;
    c = mix(nc, nc * (0.35 + 0.9 * key), 0.5);
  }
  gl_FragColor = vec4(c, 1.0);
}`;

  const VS_BLIT = `
attribute vec2 aPos;
varying vec2 vUv;
void main(){
  vUv = aPos * 0.5 + 0.5;
  gl_Position = vec4(aPos, 0.0, 1.0);
}`;

  /* Two textures come out of the renderer and they are NOT oriented alike:
       - the scene FBO is in GL orientation already: v=0 is the bottom, which
         is where the screen's v=0 is, so it must be sampled unflipped.
       - the HUD is a 2D canvas, whose row 0 is the top; texImage2D puts that
         at v=0, so it must be sampled flipped or the text is upside down.
     uFlip picks between them. Getting this wrong renders the whole scene
     upside down, so it is a uniform rather than a hardcoded constant. */
  const FS_BLIT = `
precision mediump float;
varying vec2 vUv;
uniform sampler2D uTex;
uniform float uFlip;
void main(){
  float v = uFlip > 0.5 ? 1.0 - vUv.y : vUv.y;
  gl_FragColor = texture2D(uTex, vec2(vUv.x, v));
}`;

  global.SH = {
    VS_SCENE, FS_SCENE, FS_SHADOW, VS_FLOOR, FS_FLOOR, VS_SKY, FS_SKY,
    VS_RING, FS_RING, VS_POST, FS_POST, VS_MIN, FS_MIN, VS_BLIT, FS_BLIT
  };
})(window);
