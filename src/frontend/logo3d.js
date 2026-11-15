// logo3d.js — 3D particle logo for Collectivize (v8)
//
// Three states, drawn in dots, shown in the order set by `sequence`:
//   logo    — sampled from your image, so its colors are kept exactly
//   word    — the node-style wordmark COLLECTIVIZE
//   network — a 3D node network
// Outside the logo, each color has a job:
//   blue  -> the thin lines (letter strokes, network links)
//   red   -> the circles (rings at the ends of letter strokes, network nodes)
//   green -> disappears
// - It moves to the next state every `interval` ms. Nothing the visitor does
//   (clicking, dragging) changes that timing.
// - Every state tilts gently toward the cursor and can be dragged to tilt it.
//   Tilt is deliberately heavy: it is limited to `maxTilt` and eases back to
//   center, so it can't be flung around or flipped.
// - Click the logo to spin it like a fidget spinner (up to `maxSpin`).
// - Shift + drag (or right-drag) moves it.
//
// Usage: the home page (index.html) imports initLogo3D and calls it with
// the container and the logo image (public/brand/logo.png). See landing.css
// for the box it fills.

import * as THREE from 'https://cdn.jsdelivr.net/npm/three@0.160.0/build/three.module.js';

const TAU = Math.PI * 2;
const GREEN = 0, BLUE = 1, RED = 2;

const DEFAULTS = {
  container: '#logo3d',
  logoSrc: './collectivize-logo.png', // logo only (no wordmark), same origin as the page
  word: 'COLLECTIVIZE',               // letters available: C E I L O T V Z
  interval: 15000,                    // ms between switches
  sequence: ['logo', 'word', 'network'], // order of states; remove one to skip it
  morphDuration: 1.8,                 // seconds
  particleCount: 11000,
  pointSize: 0.03,                   // dot size in scene units
  green: 'hide',                      // 'hide' = disappear outside the logo, 'depth' = layer behind the word
  // Share of the dots given to each color. Blue gets more than its area in the logo
  // so the letter lines come out solid.
  shares: { blue: 0.3, red: 0.24, green: 0.46 },
  // Reference colors used to decide which part of the logo each pixel belongs to.
  roleColors: { green: '#4CA289', blue: '#4A6CEC', red: '#D4503A' },
  flick: 11,                          // spin speed added per click (radians/second); 0 = no spin
  maxSpin: 40,                        // top spin speed (radians/second)
  friction: 0.9,                      // higher = the spin dies out faster
  follow: 0.3,                        // how far it tilts toward the cursor (radians); 0 to disable
  dragSpeed: 0.004,                   // tilt per pixel dragged (lower = heavier)
  maxTilt: 0.85,                      // furthest it can be tilted by dragging (radians, about 50 degrees)
  recenter: 0.6,                      // how quickly it eases back to center after a drag; 0 = stays put
  idleSway: 0.12,                     // gentle side-to-side turn so the depth shows; 0 to disable
};

// Letterforms on a 1-wide, 1-tall grid (y up). Each letter: strokes (polylines) and
// node positions (the ringed dots at stroke ends), matching the wordmark's style.
const LETTERS = {
  C: { w: 1, strokes: [[[1, 1], [0, 1], [0, 0], [1, 0]]], nodes: [[1, 1], [0, 1], [0, 0], [1, 0]] },
  O: { w: 1, strokes: [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]], nodes: [[0, 0], [1, 0], [1, 1], [0, 1]] },
  L: { w: 0.82, strokes: [[[0, 1], [0, 0], [0.82, 0]]], nodes: [[0, 1], [0, 0], [0.82, 0]] },
  E: {
    w: 1,
    strokes: [[[1, 1], [0, 1], [0, 0], [1, 0]], [[0, 0.5], [0.74, 0.5]]],
    nodes: [[1, 1], [0, 1], [0, 0], [1, 0], [0, 0.5], [0.74, 0.5]],
  },
  T: { w: 1, strokes: [[[0, 1], [1, 1]], [[0.5, 1], [0.5, 0]]], nodes: [[0, 1], [1, 1], [0.5, 0]] },
  I: { w: 0, strokes: [[[0, 1], [0, 0]]], nodes: [[0, 1], [0, 0]] },
  V: { w: 1, strokes: [[[0, 1], [0.5, 0], [1, 1]]], nodes: [[0, 1], [0.5, 0], [1, 1]] },
  Z: { w: 1, strokes: [[[0, 1], [1, 1], [0, 0], [1, 0]]], nodes: [[0, 1], [1, 1], [0, 0], [1, 0]] },
};

export async function initLogo3D(options = {}) {
  const o = { ...DEFAULTS, ...options };
  o.shares = { ...DEFAULTS.shares, ...(options.shares || {}) };
  o.roleColors = { ...DEFAULTS.roleColors, ...(options.roleColors || {}) };
  const el = typeof o.container === 'string' ? document.querySelector(o.container) : o.container;
  if (!el) throw new Error(`logo3d: container not found (${o.container})`);
  if (el.clientHeight === 0) el.style.height = '380px';

  const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const N = o.particleCount;
  const WORD_WIDTH = 3.6;
  const refs = [hexToRgb(o.roleColors.green), hexToRgb(o.roleColors.blue), hexToRgb(o.roleColors.red)];

  // ---------- scene ----------
  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  const canvas = renderer.domElement;
  canvas.style.cssText = 'width:100%;height:100%;display:block;touch-action:none;cursor:grab';
  el.appendChild(canvas);

  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(45, 1, 0.1, 100);
  const outer = new THREE.Group(); // tilt (drag, cursor) and position (pan)
  const inner = new THREE.Group(); // fidget spin, in the logo's own plane
  outer.add(inner);
  scene.add(outer);

  function resize() {
    const w = Math.max(1, el.clientWidth);
    const h = Math.max(1, el.clientHeight);
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    const fitZ = (WORD_WIDTH * 1.12) / (2 * Math.tan((camera.fov * Math.PI) / 360) * camera.aspect);
    camera.position.z = Math.max(4.2, fitZ);
    camera.updateProjectionMatrix();
  }
  resize();
  const ro = new ResizeObserver(resize);
  ro.observe(el);

  // ---------- targets ----------
  const samples = await sampleImage(o.logoSrc, refs).catch((err) => {
    console.warn('logo3d: could not load the logo image; using a placeholder ring.', err);
    return null;
  });
  // Dots are stored green first, then blue, then red.
  const nBlue = Math.round(N * o.shares.blue);
  const nRed = Math.round(N * o.shares.red);
  const nGreen = Math.max(0, N - nBlue - nRed);
  const roles = new Uint8Array(N);
  roles.fill(GREEN, 0, nGreen);
  roles.fill(BLUE, nGreen, nGreen + nBlue);
  roles.fill(RED, nGreen + nBlue, N);
  const counts = [nGreen, nBlue, nRed];
  const starts = [0, nGreen, nGreen + nBlue];

  // Each color's dots are spread evenly over that color's area of the logo
  // (a jittered honeycomb grid), so there are no clumps or bare patches.
  const colors = new Float32Array(N * 3);
  const logo = new Float32Array(N * 3);
  for (let role = 0; role < 3; role++) {
    const pts = samples ? evenPoints(samples, role, counts[role]) : [];
    for (let j = 0; j < counts[role]; j++) {
      const i = starts[role] + j;
      let x, y, c;
      if (pts.length) {
        const s = pts[j % pts.length];
        x = s.x * 1.3;
        y = s.y * 1.3;
        c = s.c || refs[role];
      } else {
        const a = (j / counts[role]) * TAU;
        const r = 0.9 + Math.random() * 0.25;
        x = Math.cos(a) * r;
        y = Math.sin(a) * r;
        c = refs[role];
      }
      logo.set([x, y, (Math.random() - 0.5) * 0.24], i * 3);
      colors.set(c, i * 3);
    }
  }

  const wordShape = makeWord(o.word, roles, WORD_WIDTH, o.green);
  const word = wordShape.positions;
  const wordScale = wordShape.scale;
  const logoScale = new Float32Array(N).fill(1);
  const networkShape = makeNetwork(roles, o.green);
  const shapes = {
    logo: { positions: logo, scale: logoScale },
    word: { positions: word, scale: wordScale },
    network: networkShape,
  };
  const sequence = o.sequence.filter((name) => shapes[name]);
  if (!sequence.length) sequence.push('logo');

  // ---------- particles ----------
  const pos = new Float32Array(shapes[sequence[0]].positions);
  const scale = new Float32Array(shapes[sequence[0]].scale);
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.BufferAttribute(pos, 3));
  geometry.setAttribute('aColor', new THREE.BufferAttribute(colors, 3));
  geometry.setAttribute('aScale', new THREE.BufferAttribute(scale, 1));
  const material = new THREE.ShaderMaterial({
    uniforms: { uSize: { value: o.pointSize }, uScale: { value: 1 } },
    vertexShader: `
      attribute vec3 aColor;
      attribute float aScale;
      uniform float uSize;
      uniform float uScale;
      varying vec3 vColor;
      void main() {
        vColor = aColor;
        // Browsers draw a point at least 1px wide, so hidden dots (scale 0)
        // are moved off screen instead.
        if (aScale <= 0.0) { gl_Position = vec4(2.0, 2.0, 2.0, 1.0); gl_PointSize = 0.0; return; }
        vec4 mv = modelViewMatrix * vec4(position, 1.0);
        gl_PointSize = uSize * aScale * uScale / -mv.z;
        gl_Position = projectionMatrix * mv;
      }`,
    // Solid round dots. Depth is written so nearer dots properly cover farther ones.
    fragmentShader: `
      varying vec3 vColor;
      void main() {
        if (length(gl_PointCoord - 0.5) > 0.5) discard;
        gl_FragColor = vec4(vColor, 1.0);
      }`,
  });
  const points = new THREE.Points(geometry, material);
  points.frustumCulled = false;
  inner.add(points);

  const delay = new Float32Array(N);
  const burst = new Float32Array(N * 3);
  for (let i = 0; i < N; i++) {
    delay[i] = Math.random();
    burst.set(randomUnit(), i * 3);
  }

  // ---------- state ----------
  let index = 0;
  let current = sequence[0];
  const from = new Float32Array(N * 3);
  const scaleFrom = new Float32Array(N);
  let to = shapes[current].positions;
  let scaleTo = shapes[current].scale;
  let morphT = 1;
  let sinceMorph = 0;
  let time = 0;

  let spin = 0;       // fidget angle
  let spinVel = 0;    // radians/second
  let rotX = 0, rotY = 0;       // tilt from dragging
  let velX = 0, velY = 0;       // tilt inertia
  let panX = 0, panY = 0;       // position from shift-drag
  let aimX = 0, aimY = 0;       // where the cursor is, -1..1 from the logo's center
  let followX = 0, followY = 0; // eased cursor tilt
  let swayAmount = o.idleSway;

  function goTo(name) {
    if (name === current && morphT >= 1) return;
    from.set(pos);
    scaleFrom.set(scale);
    if (!shapes[name]) return;
    to = shapes[name].positions;
    scaleTo = shapes[name].scale;
    current = name;
    morphT = reduced ? 0.999 : 0;
    sinceMorph = 0;
  }
  const toggle = () => {
    index = (index + 1) % sequence.length;
    goTo(sequence[index]);
  };
  const clampTilt = (v) => Math.max(-o.maxTilt, Math.min(o.maxTilt, v));

  // ---------- input ----------
  let drag = null;
  const worldPerPixel = () =>
    (2 * Math.tan((camera.fov * Math.PI) / 360) * camera.position.z) / Math.max(1, el.clientHeight);

  const onDown = (e) => {
    drag = { x: e.clientX, y: e.clientY, sx: e.clientX, sy: e.clientY, pan: e.shiftKey || e.button === 2, moved: false };
    canvas.setPointerCapture(e.pointerId);
    canvas.style.cursor = 'grabbing';
    velX = velY = 0;
  };
  const onMove = (e) => {
    if (!drag) return;
    const dx = e.clientX - drag.x;
    const dy = e.clientY - drag.y;
    drag.x = e.clientX;
    drag.y = e.clientY;
    if (Math.hypot(e.clientX - drag.sx, e.clientY - drag.sy) > 5) drag.moved = true;
    if (!drag.moved) return;
    if (drag.pan) {
      const k = worldPerPixel();
      panX += dx * k;
      panY -= dy * k;
    } else {
      rotY = clampTilt(rotY + dx * o.dragSpeed);
      rotX = clampTilt(rotX + dy * o.dragSpeed);
      velY = dx * o.dragSpeed * 15;
      velX = dy * o.dragSpeed * 15;
    }
  };
  const onUp = () => {
    if (!drag) return;
    const wasClick = !drag.moved;
    drag = null;
    canvas.style.cursor = 'grab';
    // A click (no drag) flicks the spinner. It never triggers or delays the morph.
    if (wasClick && current === 'logo' && !reduced) {
      spinVel = Math.min(o.maxSpin, spinVel + o.flick);
    }
  };
  const onAim = (e) => {
    const r = el.getBoundingClientRect();
    aimX = Math.max(-1, Math.min(1, (e.clientX - (r.left + r.width / 2)) / (r.width / 2)));
    aimY = Math.max(-1, Math.min(1, (e.clientY - (r.top + r.height / 2)) / (r.height / 2)));
  };
  const onMenu = (e) => e.preventDefault();
  canvas.addEventListener('pointerdown', onDown);
  canvas.addEventListener('pointermove', onMove);
  canvas.addEventListener('pointerup', onUp);
  canvas.addEventListener('pointercancel', onUp);
  canvas.addEventListener('contextmenu', onMenu);
  window.addEventListener('pointermove', onAim);

  let visible = true;
  const io = new IntersectionObserver((entries) => { visible = entries[0].isIntersecting; });
  io.observe(el);

  // ---------- loop ----------
  const clock = new THREE.Clock();
  renderer.setAnimationLoop(() => {
    const dt = Math.min(clock.getDelta(), 0.05);
    if (document.hidden || !visible) return;
    time += dt;

    // Fixed timer: clicks and drags never speed it up, slow it down, or reset it.
    if (!reduced) {
      sinceMorph += dt * 1000;
      if (sinceMorph >= o.interval && morphT >= 1) toggle();
    }

    if (morphT < 1) {
      morphT = Math.min(1, morphT + dt / o.morphDuration);
      for (let i = 0; i < N; i++) {
        let p = (morphT - delay[i] * 0.4) / 0.6;
        p = p < 0 ? 0 : p > 1 ? 1 : p;
        const e = p * p * (3 - 2 * p);
        const b = Math.sin(Math.PI * e) * 0.45;
        const j = i * 3;
        pos[j] = from[j] + (to[j] - from[j]) * e + burst[j] * b;
        pos[j + 1] = from[j + 1] + (to[j + 1] - from[j + 1]) * e + burst[j + 1] * b;
        pos[j + 2] = from[j + 2] + (to[j + 2] - from[j + 2]) * e + burst[j + 2] * b;
        scale[i] = scaleFrom[i] + (scaleTo[i] - scaleFrom[i]) * e;
      }
      geometry.attributes.position.needsUpdate = true;
      geometry.attributes.aScale.needsUpdate = true;
    }

    // Fidget spin with friction.
    if (current === 'logo') {
      spin += spinVel * dt;
      spinVel *= Math.exp(-o.friction * dt);
      if (Math.abs(spinVel) < 0.02) spinVel = 0;
    } else {
      // Other states settle upright.
      spinVel = 0;
      const upright = Math.round(spin / TAU) * TAU;
      spin += (upright - spin) * Math.min(1, dt * 4);
    }

    // Tilt: a little inertia after a drag, limited to maxTilt, easing back to center.
    if (!drag) {
      rotX = clampTilt(rotX + velX * dt);
      rotY = clampTilt(rotY + velY * dt);
      const k = Math.exp(-5 * dt);
      velX *= k;
      velY *= k;
      const back = Math.min(1, dt * o.recenter);
      rotX += (0 - rotX) * back;
      rotY += (0 - rotY) * back;
    }

    // Follow the cursor (a little less for the word, so it stays readable).
    const reach = reduced ? 0 : o.follow * (current === 'word' ? 0.6 : 1);
    followX += (aimY * reach - followX) * Math.min(1, dt * 4);
    followY += (aimX * reach - followY) * Math.min(1, dt * 4);

    // The network sways a bit more so its depth shows.
    swayAmount += ((current === 'network' ? 0.32 : o.idleSway) - swayAmount) * Math.min(1, dt * 2);
    const sway = reduced ? 0 : Math.sin(time * 0.7) * swayAmount;
    inner.rotation.z = -spin;
    outer.rotation.x = rotX + followX;
    outer.rotation.y = rotY + followY + sway;
    outer.position.set(panX, panY, 0);
    material.uniforms.uScale.value = canvas.height / (2 * Math.tan((camera.fov * Math.PI) / 360));


    renderer.render(scene, camera);
  });

  return {
    goTo,
    toggle,
    spin: (speed = o.flick) => { spinVel += speed; },
    reset() { rotX = rotY = velX = velY = panX = panY = 0; },
    destroy() {
      renderer.setAnimationLoop(null);
      ro.disconnect();
      io.disconnect();
      canvas.removeEventListener('pointerdown', onDown);
      canvas.removeEventListener('pointermove', onMove);
      canvas.removeEventListener('pointerup', onUp);
      canvas.removeEventListener('pointercancel', onUp);
      canvas.removeEventListener('contextmenu', onMenu);
      window.removeEventListener('pointermove', onAim);
      geometry.dispose();
      material.dispose();
      renderer.dispose();
      canvas.remove();
    },
  };
}

// ---------- logo sampling ----------

// Reads the logo into a small grid. Returns { w, h, data, roleMap, cx, cy, half }:
// roleMap holds, per pixel, which reference color (green, blue, red) it is closest
// to, or -1 for background. Handles a transparent background, or a solid one (the
// corner pixel is taken as the background color and anything close to it is skipped).
async function sampleImage(src, refs) {
  const img = new Image();
  img.src = src;
  await img.decode();
  const iw = img.naturalWidth || 512;
  const ih = img.naturalHeight || 512;
  const k0 = 240 / Math.max(iw, ih);
  const w = Math.max(1, Math.round(iw * k0));
  const h = Math.max(1, Math.round(ih * k0));
  const cv = document.createElement('canvas');
  cv.width = w;
  cv.height = h;
  const ctx = cv.getContext('2d', { willReadFrequently: true });
  ctx.drawImage(img, 0, 0, w, h);
  const d = ctx.getImageData(0, 0, w, h).data;

  const solidBg = d[3] > 200;
  const bg = [d[0], d[1], d[2]];
  const roleMap = new Int8Array(w * h).fill(-1);
  let found = 0;
  let minX = w, maxX = 0, minY = h, maxY = 0;
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      const k = (y * w + x) * 4;
      if (d[k + 3] < 128) continue;
      if (solidBg) {
        const dist = Math.abs(d[k] - bg[0]) + Math.abs(d[k + 1] - bg[1]) + Math.abs(d[k + 2] - bg[2]);
        if (dist < 60) continue;
      }
      const r = d[k] / 255, g = d[k + 1] / 255, b = d[k + 2] / 255;
      let role = 0, best = Infinity;
      for (let i = 0; i < refs.length; i++) {
        const e = (r - refs[i][0]) ** 2 + (g - refs[i][1]) ** 2 + (b - refs[i][2]) ** 2;
        if (e < best) { best = e; role = i; }
      }
      roleMap[y * w + x] = role;
      found++;
      if (x < minX) minX = x;
      if (x > maxX) maxX = x;
      if (y < minY) minY = y;
      if (y > maxY) maxY = y;
    }
  }
  if (!found) throw new Error('no visible pixels found in logo');
  return {
    w, h, data: d, roleMap,
    cx: (minX + maxX) / 2,
    cy: (minY + maxY) / 2,
    half: Math.max(maxX - minX, maxY - minY) / 2 || 1,
  };
}

// Returns n points spread evenly over the pixels of one role: [{x, y, c}] with
// x, y in -1..1 (y up) and c = that pixel's color. If the logo has no pixels of
// that role, it spreads over the whole logo instead and leaves c undefined.
function evenPoints(img, role, n) {
  const { w, h, data, roleMap, cx, cy, half } = img;
  let area = 0;
  for (let i = 0; i < roleMap.length; i++) if (roleMap[i] === role) area++;
  const any = area === 0;
  if (any) for (let i = 0; i < roleMap.length; i++) if (roleMap[i] >= 0) area++;
  if (!area || !n) return [];
  const match = (v) => (any ? v >= 0 : v === role);
  const make = (fx, fy, px, py) => {
    const k = (py * w + px) * 4;
    return {
      x: (fx - cx) / half,
      y: -(fy - cy) / half,
      c: any ? undefined : [data[k] / 255, data[k + 1] / 255, data[k + 2] / 255],
    };
  };

  // Honeycomb grid sized so about n grid points land inside the area.
  const step = Math.sqrt(area / (n * 0.866));
  const out = [];
  let row = 0;
  for (let gy = step * 0.5; gy < h; gy += step * 0.866, row++) {
    for (let gx = row % 2 ? step * 0.75 : step * 0.25; gx < w; gx += step) {
      const fx = gx + (Math.random() - 0.5) * step * 0.3;
      const fy = gy + (Math.random() - 0.5) * step * 0.3;
      const px = Math.min(w - 1, Math.max(0, Math.round(fx)));
      const py = Math.min(h - 1, Math.max(0, Math.round(fy)));
      if (match(roleMap[py * w + px])) out.push(make(fx, fy, px, py));
    }
  }
  for (let i = out.length - 1; i > 0; i--) {
    const j = (Math.random() * (i + 1)) | 0;
    [out[i], out[j]] = [out[j], out[i]];
  }
  if (out.length > n) out.length = n;
  if (out.length < n) {
    const pool = [];
    for (let i = 0; i < roleMap.length; i++) if (match(roleMap[i])) pool.push(i);
    while (out.length < n) {
      const i = pool[(Math.random() * pool.length) | 0];
      const px = i % w;
      const py = (i / w) | 0;
      out.push(make(px + Math.random() - 0.5, py + Math.random() - 0.5, px, py));
    }
  }
  return out;
}

// ---------- node-style wordmark ----------

// Places every dot in the wordmark according to its color:
//   blue  -> along the letter strokes, evenly spaced in three tight rows
//   red   -> on the rings at the stroke ends, evenly spaced around two rings.
//            Nothing else is placed inside or behind a ring, so its center is empty.
//   green -> a looser copy of the letters set back in depth ('depth'),
//            or shrunk to nothing ('hide')
// Returns { positions, scale } (scale = dot size multiplier per dot).
function makeWord(text, roles, width, greenMode) {
  const n = roles.length;
  const HEIGHT = 1.45; // letter height relative to letter width
  const GAP = 0.62;
  const segs = [];
  const nodes = [];
  let cursor = 0;
  for (const ch of text.toUpperCase()) {
    const L = LETTERS[ch];
    if (!L) { cursor += 0.6; continue; }
    for (const stroke of L.strokes) {
      for (let i = 0; i < stroke.length - 1; i++) {
        segs.push([
          cursor + stroke[i][0], stroke[i][1] * HEIGHT,
          cursor + stroke[i + 1][0], stroke[i + 1][1] * HEIGHT,
        ]);
      }
    }
    for (const p of L.nodes) nodes.push([cursor + p[0], p[1] * HEIGHT]);
    cursor += L.w + GAP;
  }
  const total = Math.max(0.001, cursor - GAP);
  const k = width / total;
  const ox = -total / 2;
  const oy = -HEIGHT / 2;

  const lengths = segs.map((s) => Math.hypot(s[2] - s[0], s[3] - s[1]));
  const sum = lengths.reduce((a, b) => a + b, 0) || 1;
  const onStroke = (spread) => {
    let r = Math.random() * sum;
    let s = segs[segs.length - 1];
    for (let i = 0; i < segs.length; i++) {
      r -= lengths[i];
      if (r <= 0) { s = segs[i]; break; }
    }
    const t = Math.random();
    return [
      (s[0] + (s[2] - s[0]) * t + ox) * k + (Math.random() - 0.5) * spread,
      (s[1] + (s[3] - s[1]) * t + oy) * k + (Math.random() - 0.5) * spread,
    ];
  };

  // Even placement for the lines and rings.
  const counts = [0, 0, 0];
  for (let i = 0; i < n; i++) counts[roles[i]]++;
  const seen = [0, 0, 0];
  const ROWS = 3;
  const perRow = Math.max(1, Math.ceil(counts[BLUE] / ROWS));
  const perNode = Math.max(1, Math.ceil(counts[RED] / Math.max(1, nodes.length)));
  const perRing = Math.max(1, Math.ceil(perNode / 2));
  const alongStroke = (dist, offset) => {
    let s = segs[segs.length - 1];
    let r = dist;
    for (let i = 0; i < segs.length; i++) {
      if (r <= lengths[i]) { s = segs[i]; break; }
      r -= lengths[i];
    }
    const len = Math.hypot(s[2] - s[0], s[3] - s[1]) || 1;
    const t = Math.min(1, Math.max(0, r / len));
    const nx = -(s[3] - s[1]) / len;
    const ny = (s[2] - s[0]) / len;
    return [
      (s[0] + (s[2] - s[0]) * t + ox) * k + nx * offset + (Math.random() - 0.5) * 0.004,
      (s[1] + (s[3] - s[1]) * t + oy) * k + ny * offset + (Math.random() - 0.5) * 0.004,
    ];
  };

  // Keep the lines and the back layer clear of every ring.
  const RING_INNER = 0.037;
  const RING_OUTER = 0.048;
  const CLEAR_LINE = RING_OUTER + 0.012;  // lines stop just outside the ring
  const CLEAR_BACK = RING_OUTER + 0.04;   // back layer keeps a wider gap, so tilting never shows it through a ring
  const centers = nodes.map((p) => [(p[0] + ox) * k, (p[1] + oy) * k]);
  const nearNode = (x, y, radius) => {
    for (const c of centers) if (Math.hypot(x - c[0], y - c[1]) < radius) return true;
    return false;
  };
  // Distances along the strokes that are outside every ring.
  const open = [];
  const STEPS = 6000;
  for (let i = 0; i < STEPS; i++) {
    const dist = ((i + 0.5) / STEPS) * sum;
    const q = alongStroke(dist, 0);
    if (!nearNode(q[0], q[1], CLEAR_LINE)) open.push(dist);
  }
  const openAt = (f) => (open.length ? open[Math.min(open.length - 1, Math.floor(f * open.length))] : f * sum);

  const positions = new Float32Array(n * 3);
  const scale = new Float32Array(n).fill(1);
  const empty = !segs.length || !nodes.length;
  for (let i = 0; i < n; i++) {
    let xy = [0, 0];
    let z = (Math.random() - 0.5) * 0.08;
    if (!empty) {
      if (roles[i] === BLUE) {
        const j = seen[BLUE]++;
        xy = alongStroke(openAt((((j / ROWS) | 0) + 0.5) / perRow), ((j % ROWS) - 1) * 0.012);
      } else if (roles[i] === RED) {
        const j = seen[RED]++;
        const p = nodes[j % nodes.length];
        const m = (j / nodes.length) | 0;
        const ringRow = m % 2;
        const ang = (((m >> 1) + ringRow * 0.5) / perRing) * TAU;
        const r = ringRow ? RING_OUTER : RING_INNER;
        xy = [(p[0] + ox) * k + Math.cos(ang) * r, (p[1] + oy) * k + Math.sin(ang) * r];
        z += 0.03; // rings sit just in front of the lines
      } else {
        for (let tries = 0; tries < 12; tries++) {
          xy = onStroke(0.05);
          if (!nearNode(xy[0], xy[1], CLEAR_BACK)) break;
        }
        z = -0.2 - Math.random() * 0.35; // set back behind the letters
        scale[i] = greenMode === 'hide' ? 0 : 0.85;
      }
    }
    positions.set([xy[0], xy[1], z], i * 3);
  }
  return { positions, scale };
}

// ---------- node network ----------

// A 3D node network: red dots form the nodes (small spheres, which read as circles
// from any angle), blue dots form thin links between nearby nodes, green is hidden.
// Returns { positions, scale }.
function makeNetwork(roles, greenMode) {
  const n = roles.length;
  const K = 14;        // number of nodes
  const NODE = 0.085;  // node radius
  const dist3 = (p, q) => Math.hypot(p[0] - q[0], p[1] - q[1], p[2] - q[2]);

  const nodes = [];
  for (let guard = 0; nodes.length < K && guard < 5000; guard++) {
    const v = randomUnit();
    const r = 1.25 * Math.cbrt(0.2 + Math.random() * 0.8);
    const p = [v[0] * r * 1.15, v[1] * r, v[2] * r * 0.8];
    if (nodes.every((q) => dist3(p, q) > 0.5)) nodes.push(p);
  }

  // Link each node to its two nearest neighbors (no duplicate links).
  const links = [];
  const have = new Set();
  nodes.forEach((p, i) => {
    nodes
      .map((q, j) => ({ j, d: dist3(p, q) }))
      .filter((e) => e.j !== i)
      .sort((a, b) => a.d - b.d)
      .slice(0, 2)
      .forEach((e) => {
        const key = Math.min(i, e.j) + '-' + Math.max(i, e.j);
        if (!have.has(key)) { have.add(key); links.push([i, e.j]); }
      });
  });
  // Links stop at the surface of each node instead of running through it.
  const spans = links.map(([a, b]) => {
    const A = nodes[a], B = nodes[b];
    const d = dist3(A, B) || 1;
    const t0 = Math.min(0.5, (NODE + 0.015) / d);
    return { A, B, t0, len: d * (1 - 2 * t0) };
  });
  const total = spans.reduce((sum, sp) => sum + sp.len, 0) || 1;

  const counts = [0, 0, 0];
  for (let i = 0; i < n; i++) counts[roles[i]]++;
  const seen = [0, 0, 0];
  const perNode = Math.max(1, Math.ceil(counts[RED] / Math.max(1, nodes.length)));

  const positions = new Float32Array(n * 3);
  const scale = new Float32Array(n).fill(1);
  for (let i = 0; i < n; i++) {
    let x = 0, y = 0, z = 0;
    if (roles[i] === BLUE && spans.length) {
      // evenly spaced along the links, kept thin
      const j = seen[BLUE]++;
      let r = ((j + 0.5) / counts[BLUE]) * total;
      let sp = spans[spans.length - 1];
      for (const cand of spans) {
        if (r <= cand.len) { sp = cand; break; }
        r -= cand.len;
      }
      const t = sp.t0 + Math.min(1, Math.max(0, r / (sp.len || 1))) * (1 - 2 * sp.t0);
      const jit = () => (Math.random() - 0.5) * 0.008;
      x = sp.A[0] + (sp.B[0] - sp.A[0]) * t + jit();
      y = sp.A[1] + (sp.B[1] - sp.A[1]) * t + jit();
      z = sp.A[2] + (sp.B[2] - sp.A[2]) * t + jit();
      scale[i] = 0.8;
    } else if (roles[i] === RED && nodes.length) {
      // evenly spaced over each node's surface
      const j = seen[RED]++;
      const c = nodes[j % nodes.length];
      const m = (j / nodes.length) | 0;
      const yy = 1 - (2 * (m + 0.5)) / perNode;
      const rr = Math.sqrt(Math.max(0, 1 - yy * yy));
      const phi = m * 2.399963;
      x = c[0] + Math.cos(phi) * rr * NODE;
      y = c[1] + yy * NODE;
      z = c[2] + Math.sin(phi) * rr * NODE;
    } else if (nodes.length) {
      // green: tucked inside a node and hidden (or a faint halo in 'depth' mode)
      const c = nodes[(Math.random() * nodes.length) | 0];
      const v = randomUnit();
      const r = greenMode === 'hide' ? 0 : NODE * 1.7;
      x = c[0] + v[0] * r;
      y = c[1] + v[1] * r;
      z = c[2] + v[2] * r;
      scale[i] = greenMode === 'hide' ? 0 : 0.6;
    }
    positions.set([x, y, z], i * 3);
  }
  return { positions, scale };
}

// ---------- small utilities ----------

function randomUnit() {
  const u = Math.random() * 2 - 1;
  const t = Math.random() * TAU;
  const s = Math.sqrt(1 - u * u);
  return [s * Math.cos(t), u, s * Math.sin(t)];
}

function hexToRgb(hex) {
  const v = parseInt(String(hex).replace('#', ''), 16) || 0;
  return [((v >> 16) & 255) / 255, ((v >> 8) & 255) / 255, (v & 255) / 255];
}
