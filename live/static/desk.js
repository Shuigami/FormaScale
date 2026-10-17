'use strict';

const $ = (id) => document.getElementById(id);
const scene = $('scene');
const ctx = scene.getContext('2d');
const preview = $('live-preview');
const photoCanvas = $('photo-canvas');
const photoCtx = photoCanvas.getContext('2d');

let points = [];
let cameras = [];
let images = [];
let selected = null;
let projected = [];
let photoEntry = null;
let photoImage = null;
let photoToken = 0;
let yaw = 0.15;
let pitch = -0.25;
let zoom = 1;
let focus = [0, 0, 0];
let dist = 2.2;
let drag = null;
let previewTimer = null;
let viewMode = 'live';
const keys = new Set();
let moveRaf = 0;

function lookDir() {
  const cy = Math.cos(yaw);
  const sy = Math.sin(yaw);
  const cp = Math.cos(pitch);
  const sp = Math.sin(pitch);
  return [-sy * cp, sp, cy * cp];
}

function rightDir() {
  // Desk mirrors screen X, so FPS "right" is flipped.
  const cy = Math.cos(yaw);
  const sy = Math.sin(yaw);
  return [-cy, 0, -sy];
}

function format(n, digits = 0) {
  return n.toLocaleString('fr-FR', { maximumFractionDigits: digits });
}

function setBadge(text, tone) {
  $('badge').textContent = text;
  $('badge').dataset.tone = tone || 'warn';
}

function setView(mode) {
  viewMode = mode;
  $('tab-live').classList.toggle('active', mode === 'live');
  $('tab-keyframe').classList.toggle('active', mode === 'keyframe');
  $('live-pane').hidden = mode !== 'live';
  $('keyframe-pane').hidden = mode !== 'keyframe';
  $('photo-status').textContent = mode === 'live' ? 'Flux live' : 'Keyframe raccordée';
  if (mode === 'keyframe') drawPhoto();
}

function coordinates() {
  if (!points.length) return { center: [0, 0, 0], scale: 1 };
  const axes = [0, 1, 2].map((axis) => points.map((p) => p.xyz[axis]).sort((a, b) => a - b));
  const center = axes.map((values) => values[Math.floor(values.length / 2)]);
  const extent = Math.max(
    ...axes.map((values) => values[Math.floor((values.length - 1) * 0.95)] - values[Math.floor((values.length - 1) * 0.05)]),
    1e-8,
  );
  return { center, scale: extent };
}

function draw() {
  const rect = scene.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  scene.width = Math.max(1, Math.floor(rect.width * dpr));
  scene.height = Math.max(1, Math.floor(rect.height * dpr));
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  const width = rect.width;
  const height = rect.height;
  ctx.clearRect(0, 0, width, height);
  projected = [];

  if (!points.length) {
    ctx.fillStyle = '#819ca9';
    ctx.font = '13px Segoe UI';
    /* empty cloud: plate underneath carries the atlas */
    return;
  }

  const { center, scale } = coordinates();
  const L = lookDir();
  const cam = [
    focus[0] - L[0] * dist,
    focus[1] - L[1] * dist,
    focus[2] - L[2] * dist,
  ];
  const focal = Math.min(width, height) * 0.75 * zoom * dist;
  const project = (xyz) => {
    let [x, y, z] = xyz.map((value, axis) => (value - center[axis]) / scale);
    x -= cam[0];
    y -= cam[1];
    z -= cam[2];
    const xx = Math.cos(yaw) * x + Math.sin(yaw) * z;
    const zz = -Math.sin(yaw) * x + Math.cos(yaw) * z;
    const yy = Math.cos(pitch) * y - Math.sin(pitch) * zz;
    const depth = Math.sin(pitch) * y + Math.cos(pitch) * zz;
    if (depth <= 0.05) return { x: -1e6, y: -1e6, z: depth };
    const factor = focal / depth;
    // Mirror X so left/right matches the phone framing.
    return { x: width / 2 - xx * factor, y: height / 2 - yy * factor, z: depth };
  };

  projected = points.map((point) => ({ ...project(point.xyz), point })).sort((a, b) => a.z - b.z);
  for (const item of projected) {
    const color = item.point.color || [180, 200, 210];
    ctx.fillStyle = `rgb(${color.join(',')})`;
    ctx.fillRect(item.x - 1.5, item.y - 1.5, 3, 3);
  }

  ctx.strokeStyle = '#6ab9ee';
  for (const camera of cameras) {
    const point = project(camera.center);
    ctx.strokeRect(point.x - 3, point.y - 3, 6, 6);
  }
}

function fillPhotoSelect(preferred) {
  const previous = preferred || $('photo').value;
  $('photo').replaceChildren();
  for (const image of images) {
    const option = document.createElement('option');
    option.value = image.name;
    option.textContent = `${image.name} · ${image.observations.length} pts`;
    $('photo').append(option);
  }
  if (!images.length) return;
  if (previous && images.some((image) => image.name === previous)) {
    $('photo').value = previous;
  } else {
    $('photo').value = images[0].name;
  }
}

function loadPhoto() {
  const name = $('photo').value;
  const token = ++photoToken;
  photoEntry = images.find((image) => image.name === name) || null;
  if (!photoEntry) {
    photoImage = null;
    $('photo-detail').textContent = 'Aucune keyframe raccordée.';
    drawPhoto();
    return;
  }
  $('photo-detail').textContent = `${photoEntry.observations.length} points 3D observés dans cette keyframe.`;
  const image = new Image();
  image.onload = () => {
    if (token !== photoToken) return;
    photoImage = image;
    drawPhoto();
  };
  image.onerror = () => {
    if (token !== photoToken) return;
    photoImage = null;
    $('photo-detail').textContent = 'Keyframe inaccessible.';
    drawPhoto();
  };
  image.src = `/api/frame/${encodeURIComponent(name)}?t=${Date.now()}`;
}

function drawPhoto() {
  if (!photoImage) {
    photoCanvas.width = 640;
    photoCanvas.height = 360;
    photoCtx.clearRect(0, 0, photoCanvas.width, photoCanvas.height);
    return;
  }
  photoCanvas.width = photoImage.naturalWidth;
  photoCanvas.height = photoImage.naturalHeight;
  photoCtx.drawImage(photoImage, 0, 0);
  const scaleX = photoCanvas.width / (photoEntry?.width || photoCanvas.width);
  const scaleY = photoCanvas.height / (photoEntry?.height || photoCanvas.height);
  for (const observation of photoEntry?.observations || []) {
    const active = observation.point === selected;
    photoCtx.fillStyle = active ? '#ffb650' : '#4df0bb';
    photoCtx.beginPath();
    photoCtx.arc(
      observation.xy[0] * scaleX,
      observation.xy[1] * scaleY,
      active ? 13 : 4,
      0,
      Math.PI * 2,
    );
    photoCtx.fill();
  }
}

function selectPoint(id) {
  selected = id;
  const point = points.find((entry) => entry.id === id);
  if (!point) return;
  const views = images.filter((image) => image.observations.some((obs) => obs.point === id));
  $('selection').textContent = views.length
    ? `Point ${id} · ${point.track_length || views.length} observations · erreur ${format(point.error || 0, 3)} px`
    : `Point ${id} · aucune observation keyframe exportée`;
  setView('keyframe');
  if (views.length) {
    const current = $('photo').value;
    if (!views.some((image) => image.name === current)) {
      $('photo').value = views[0].name;
      loadPhoto();
    } else {
      drawPhoto();
    }
  } else {
    drawPhoto();
  }
  draw();
}

function applyReconstruction(event) {
  points = event.points || [];
  cameras = event.cameras || [];
  images = event.images || [];
  document.body.classList.toggle('has-cloud', points.length > 0);
  if (selected && !points.some((point) => point.id === selected)) selected = null;
  fillPhotoSelect();
  if (images.length) loadPhoto();
  applyStatus(event.status);
  draw();
  drawPhoto();
}

function formatInterval(ms) {
  return `${(ms / 1000).toLocaleString('fr-FR', { maximumFractionDigits: 1 })} s`;
}

let applyingRemoteInterval = false;

function applyInterval(ms, { push = false } = {}) {
  const value = Math.max(500, Math.min(8000, Math.round(Number(ms) || 2200)));
  applyingRemoteInterval = true;
  $('auto-interval').value = String(value);
  $('interval-label').textContent = formatInterval(value);
  applyingRemoteInterval = false;
  if (push) {
    fetch('/api/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ auto_interval_ms: value }),
    }).catch(() => {});
  }
}

function applyStatus(status) {
  if (!status) return;
  $('stat-keyframes').textContent = String(status.keyframes ?? 0);
  $('stat-points').textContent = String(status.points ?? points.length);
  $('stat-registered').textContent = String(status.registered ?? 0);
  $('status-line').textContent = status.message || '';
  if (status.state === 'running') {
    setBadge(status.mode === 'dense' ? 'Densification…' : 'Reconstruction…', 'run');
  } else if (status.state === 'ready') {
    setBadge(status.mode === 'dense' ? 'Dense à jour' : 'Nuage à jour', 'ok');
  } else if (status.state === 'error') {
    setBadge(status.mode === 'dense' ? 'Erreur dense' : 'Erreur SfM');
  } else {
    setBadge('En écoute');
  }
}

function refreshPreview() {
  if (viewMode !== 'live') return;
  preview.src = `/api/preview.jpg?t=${Date.now()}`;
  document.body.classList.add('has-live-preview');
}

function connectEvents() {
  const source = new EventSource('/api/events');
  source.onmessage = (message) => {
    let event;
    try {
      event = JSON.parse(message.data);
    } catch (_) {
      return;
    }
    if (event.type === 'meta') {
      $('session-title').textContent = `/ ${event.session}`;
      $('stat-phone').textContent = event.phone_connected ? 'Connecté' : 'Absent';
      $('stat-keyframes').textContent = String(event.keyframes ?? 0);
      if (event.auto_interval_ms != null) applyInterval(event.auto_interval_ms);
      applyStatus(event.status);
    } else if (event.type === 'settings') {
      if (event.auto_interval_ms != null) applyInterval(event.auto_interval_ms);
    } else if (event.type === 'preview') {
      $('stat-phone').textContent = 'Connecté';
      refreshPreview();
    } else if (event.type === 'keyframe') {
      $('stat-keyframes').textContent = String(event.keyframes ?? 0);
    } else if (event.type === 'cleared') {
      $('stat-keyframes').textContent = '0';
      selected = null;
      $('selection').textContent = 'Keyframes effacées — nouvelle observation.';
      setBadge('Prêt', 'ok');
      setView('live');
    } else if (event.type === 'reconstruction') {
      applyReconstruction(event);
    }
  };
  source.onerror = () => {
    setBadge('SSE interrompu');
    $('stat-phone').textContent = '…';
  };
}

async function bootstrap() {
  try {
    const response = await fetch('/api/state', { signal: AbortSignal.timeout(1500) });
    const state = await response.json();
    $('session-title').textContent = `/ ${state.session}`;
    $('stat-phone').textContent = state.phone_connected ? 'Connecté' : 'Absent';
    $('stat-keyframes').textContent = String(state.keyframes || 0);
    if (state.auto_interval_ms != null) applyInterval(state.auto_interval_ms);
    if (state.reconstruction) applyReconstruction(state.reconstruction);
    if (state.phone_connected) refreshPreview();
    connectEvents();
    previewTimer = setInterval(refreshPreview, 250);
  } catch (_) {
    setBadge('Hors ligne');
  }
}

function stepEye() {
  if (!keys.size) {
    moveRaf = 0;
    return;
  }
  const speed = keys.has('shift') ? 0.08 : 0.035;
  const right = rightDir();
  let dx = 0;
  let dy = 0;
  let dz = 0;
  // W/S : same as mouse wheel zoom (scale only, no focal/dist change).
  if (keys.has('w')) zoom = Math.min(4, zoom * 1.02);
  if (keys.has('s')) zoom = Math.max(0.3, zoom * 0.98);
  if (keys.has('d')) {
    dx += right[0];
    dz += right[2];
  }
  if (keys.has('a')) {
    dx -= right[0];
    dz -= right[2];
  }
  if (keys.has('e') || keys.has(' ')) dy += 1;
  if (keys.has('q') || keys.has('control')) dy -= 1;
  if (dx || dy || dz) {
    const len = Math.hypot(dx, dy, dz) || 1;
    focus[0] += (dx / len) * speed;
    focus[1] += (dy / len) * speed;
    focus[2] += (dz / len) * speed;
  }
  draw();
  moveRaf = requestAnimationFrame(stepEye);
}

function ensureMoveLoop() {
  if (!moveRaf) moveRaf = requestAnimationFrame(stepEye);
}

scene.addEventListener('pointerdown', (event) => {
  drag = { x: event.clientX, y: event.clientY, moved: 0 };
  scene.setPointerCapture(event.pointerId);
});
scene.addEventListener('pointermove', (event) => {
  if (!drag) return;
  const dx = event.clientX - drag.x;
  const dy = event.clientY - drag.y;
  drag.moved += Math.abs(dx) + Math.abs(dy);
  yaw += dx * 0.008;
  pitch = Math.max(-1.4, Math.min(1.4, pitch + dy * 0.008));
  drag.x = event.clientX;
  drag.y = event.clientY;
  draw();
});
scene.addEventListener('pointerup', (event) => {
  if (drag && drag.moved < 5) {
    const rect = scene.getBoundingClientRect();
    const x = event.clientX - rect.left;
    const y = event.clientY - rect.top;
    let nearest = null;
    let distance = 12;
    for (const point of projected) {
      const delta = Math.hypot(point.x - x, point.y - y);
      if (delta < distance) {
        nearest = point;
        distance = delta;
      }
    }
    if (nearest) selectPoint(nearest.point.id);
  }
  drag = null;
});
scene.addEventListener('pointercancel', () => { drag = null; });
scene.addEventListener('wheel', (event) => {
  event.preventDefault();
  zoom = Math.max(0.3, Math.min(4, zoom * (event.deltaY > 0 ? 0.9 : 1.1)));
  draw();
}, { passive: false });

photoCanvas.addEventListener('click', (event) => {
  if (!photoEntry) return;
  const rect = event.target.getBoundingClientRect();
  const x = ((event.clientX - rect.left) / rect.width) * photoEntry.width;
  const y = ((event.clientY - rect.top) / rect.height) * photoEntry.height;
  let nearest = null;
  let distance = (18 / rect.width) * photoEntry.width;
  for (const observation of photoEntry.observations) {
    const delta = Math.hypot(observation.xy[0] - x, observation.xy[1] - y);
    if (delta < distance) {
      nearest = observation;
      distance = delta;
    }
  }
  if (nearest) selectPoint(nearest.point);
});

$('tab-live').addEventListener('click', () => setView('live'));
$('tab-keyframe').addEventListener('click', () => setView('keyframe'));
$('photo').addEventListener('change', loadPhoto);

function recenter() {
  yaw = 0.15;
  pitch = -0.25;
  zoom = 1;
  focus = [0, 0, 0];
  dist = 2.2;
  draw();
}

$('reset').addEventListener('click', recenter);
window.addEventListener('keydown', (event) => {
  if (event.target && /input|select|textarea/i.test(event.target.tagName)) return;
  const key = event.key.toLowerCase();
  if (key === 'r') {
    recenter();
    return;
  }
  if (['w', 'a', 's', 'd', 'q', 'e', ' ', 'shift', 'control'].includes(key) || event.code === 'Space') {
    if (event.code === 'Space') keys.add(' ');
    else keys.add(key);
    event.preventDefault();
    ensureMoveLoop();
  }
});
window.addEventListener('keyup', (event) => {
  if (event.code === 'Space') keys.delete(' ');
  else if (event.key === 'Shift') keys.delete('shift');
  else if (event.key === 'Control') keys.delete('control');
  else keys.delete(event.key.toLowerCase());
});

$('auto-interval').addEventListener('input', () => {
  if (applyingRemoteInterval) return;
  applyInterval($('auto-interval').value, { push: true });
});

$('reconstruct').addEventListener('click', async () => {
  setBadge('Reconstruction…', 'run');
  await fetch('/api/reconstruct', { method: 'POST' });
});

$('densify').addEventListener('click', async () => {
  setBadge('Densification…', 'run');
  try {
    const response = await fetch('/api/dense', { method: 'POST' });
    const result = await response.json();
    if (!response.ok) {
      setBadge('Dense indisponible');
      $('selection').textContent = result.message || 'Sparse requis avant densification.';
      return;
    }
    $('selection').textContent = result.cached
      ? 'Chargement du fused.ply existant…'
      : 'PatchMatch GPU en cours — plusieurs minutes possibles.';
  } catch (_) {
    setBadge('Dense échoué');
  }
});

$('clear-keyframes').addEventListener('click', async () => {
  const count = Number($('stat-keyframes').textContent) || 0;
  const label = count > 0
    ? `Effacer les ${count} keyframes et recommencer ?`
    : 'Effacer les keyframes et recommencer ?';
  if (!window.confirm(label)) return;
  setBadge('Effacement…', 'run');
  try {
    const response = await fetch('/api/clear', { method: 'POST' });
    const result = await response.json();
    $('stat-keyframes').textContent = '0';
    selected = null;
    applyReconstruction({
      points: [],
      cameras: [],
      images: [],
      status: {
        state: 'idle',
        keyframes: 0,
        points: 0,
        registered: 0,
        message: 'Keyframes effacées — nouvelle observation',
      },
    });
    $('selection').textContent = result.removed
      ? `${result.removed} keyframes supprimées. Tu peux recommencer.`
      : 'Aucune keyframe à supprimer.';
    setBadge('Prêt', 'ok');
    setView('live');
  } catch (_) {
    setBadge('Effacement échoué');
  }
});

window.addEventListener('resize', () => {
  draw();
  drawPhoto();
});
preview.addEventListener('error', () => {
  preview.removeAttribute('src');
  document.body.classList.remove('has-live-preview');
});
preview.addEventListener('load', () => {
  if (preview.getAttribute('src')) document.body.classList.add('has-live-preview');
});

bootstrap();
draw();
