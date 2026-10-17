'use strict';

const catalog = window.FORMA_CATALOG || [];
const $ = (id) => document.getElementById(id);

let data = null;
let captureMeta = null;
let annotations = { A: [], B: [], C: [], D: [] };
let run = null;
let model = null;
let selected = null;
let yaw = 0.35;
let pitch = -0.45;
let zoom = 1;
let focus = [0, 0, 0];
let dist = 2.2;
let drag = null;
const keys = new Set();
let moveRaf = 0;
let projected = [];
let photoImage = null;
let photoEntry = null;
let photoToken = 0;

function lookDir() {
  const cy = Math.cos(yaw);
  const sy = Math.sin(yaw);
  const cp = Math.cos(pitch);
  const sp = Math.sin(pitch);
  return [-sy * cp, sp, cy * cp];
}

function rightDir() {
  const cy = Math.cos(yaw);
  const sy = Math.sin(yaw);
  return [cy, 0, sy];
}

const scene = $('scene');
const ctx = scene.getContext('2d');

function option(value, label) {
  const element = document.createElement('option');
  element.value = value;
  element.textContent = label;
  return element;
}

function format(n, digits = 0) {
  return n.toLocaleString('fr-FR', { maximumFractionDigits: digits });
}

function imageBase() {
  return data?.image_base || captureMeta?.image_base || `../captures/${data?.capture}/prepared/images/`;
}

function isDtu() {
  return (data?.kind || captureMeta?.kind) === 'dtu';
}

function loadScript(src) {
  return new Promise((resolve, reject) => {
    const previous = document.getElementById('forma-data-script');
    if (previous) previous.remove();
    delete window.FORMA_DATA;
    const script = document.createElement('script');
    script.id = 'forma-data-script';
    script.src = `${src}?t=${Date.now()}`;
    script.onload = () => resolve(window.FORMA_DATA);
    script.onerror = () => reject(new Error(`Impossible de charger ${src}`));
    document.head.appendChild(script);
  });
}

async function loadCapture() {
  captureMeta = catalog[Number($('capture').value)];
  $('status-badge').textContent = 'Chargement…';
  $('capture-title').textContent = `/ ${captureMeta.label}`;
  try {
    data = await loadScript(captureMeta.data_url);
    if (!data || !Array.isArray(data.runs)) throw new Error('données invalides');
    data.image_base = data.image_base || captureMeta.image_base;
    data.kind = data.kind || captureMeta.kind;
    $('run').replaceChildren();
    for (let index = 0; index < data.runs.length; index += 1) {
      $('run').append(option(index, data.runs[index].name));
    }
    const best = data.runs.reduce((bestIndex, current, index) => {
      const bestCount = Math.max(0, ...data.runs[bestIndex].models.map((item) => item.images.length));
      const currentCount = Math.max(0, ...current.models.map((item) => item.images.length));
      return currentCount > bestCount ? index : bestIndex;
    }, 0);
    $('run').value = String(Math.max(0, best));
    $('calibration').hidden = isDtu();
    $('annotation-controls').hidden = isDtu();
    $('status-badge').textContent = isDtu()
      ? 'Benchmark DTU'
      : (data.metric_scale_validated ? 'Échelle validée' : 'Géométrie en cours de validation');
    loadRun();
  } catch (error) {
    $('status-badge').textContent = 'Erreur de chargement';
    $('selection').textContent = String(error.message || error);
    data = null;
    run = null;
    model = null;
    draw();
  }
}

function loadRun() {
  if (!data) return;
  run = data.runs[Number($('run').value)];
  $('model').replaceChildren();
  const sorted = [...run.models].sort((a, b) => {
    const rank = (entry) => (entry.kind === 'dense' || entry.id === 'dense' ? 1 : 0);
    return rank(a) - rank(b) || b.images.length - a.images.length;
  });
  for (const entry of sorted) {
    const dense = entry.kind === 'dense' || entry.id === 'dense';
    const label = dense
      ? `Dense MVS · ${format(entry.points_total || entry.points.length)} pts`
      : `Modèle ${entry.id} · ${entry.images.length} photos`;
    $('model').append(option(entry.id, label));
  }
  const denseModel = sorted.find((entry) => entry.kind === 'dense' || entry.id === 'dense');
  if (denseModel) $('model').value = String(denseModel.id);
  loadModel();
}

function loadModel() {
  if (!run) return;
  model = run.models.find((entry) => String(entry.id) === $('model').value);
  selected = null;
  photoImage = null;
  photoEntry = null;
  loadAnnotations();
  $('photo').replaceChildren();
  if (!model) {
    $('selection').textContent = 'Aucun modèle reconstruit pour cette expérience.';
    draw();
    return;
  }
  for (const image of [...run.images].sort((a, b) => a.name.localeCompare(b.name))) {
    const marker = model.images.some((entry) => entry.name === image.name) ? '●' : '○';
    $('photo').append(option(image.name, `${marker} ${image.name}`));
  }
  if (model.images.length) $('photo').value = model.images[0].name;

  const summary = run.summary;
  const dense = model.kind === 'dense' || model.id === 'dense';
  const pointLabel = model.points_total && model.points_total > model.points.length
    ? `${format(model.points.length)} / ${format(model.points_total)}`
    : format(model.points.length);
  const inputImages = summary.input_images || summary.used_for_sfm || summary.keyframes || '—';
  const secondsTotal = typeof summary.seconds === 'number'
    ? summary.seconds
    : (summary.seconds?.total ?? 0);
  const stats = [
    [`${model.images.length} / ${inputImages}`, dense ? 'Vues (caméras sparse)' : 'Photos raccordées'],
    [pointLabel, dense ? 'Points dense affichés' : 'Points 3D affichés'],
    [
      dense ? 'MVS' : `${format(model.reprojection_error, 3)} px`,
      dense ? 'PatchMatch + fusion' : 'Erreur moyenne de reprojection',
    ],
    [
      `${format(secondsTotal, 1)} s`,
      dense
        ? 'Temps SfM sparse (hors densification)'
        : (summary.matching_reused
          ? 'Reprise géométrique seule'
          : (summary.extraction_reused_from ? 'Appariement + géométrie' : 'Temps de calcul CPU')),
    ],
  ];
  $('stats').replaceChildren();
  for (const [value, label] of stats) {
    const element = document.createElement('div');
    element.className = 'stat';
    const strong = document.createElement('strong');
    strong.textContent = value;
    const span = document.createElement('span');
    span.textContent = label;
    element.append(strong, span);
    $('stats').append(element);
  }

  const names = new Set(model.images.map((image) => image.name));
  $('missing').textContent = run.images
    .filter((image) => !names.has(image.name))
    .map((image) => `${image.name} (${image.connected_views} vues liées)`)
    .join(' · ') || 'Toutes les photos sont raccordées.';
  $('selection').textContent = 'Clique sur un point pour voir ses observations dans les photos.';
  $('scene-notice').textContent = dense
    ? 'Nuage dense (fused.ply sous-échantillonné). Échelle arbitraire — pas encore une mesure.'
    : (isDtu()
      ? 'Nuage SfM épars. L’échelle affichée est arbitraire jusqu’à alignement DTU ; les métriques mm sont dans le panneau DTU.'
      : 'Échelle arbitraire. Le bureau et la feuille peuvent faire partie du nuage. Aucune précision métrique n’est encore validée.');
  updateDtuPanel();
  yaw = 0.35;
  pitch = -0.45;
  zoom = 1;
  focus = [0, 0, 0];
  dist = 2.2;
  draw();
  loadPhoto();
}

function updateDtuPanel() {
  const panel = $('dtu-panel');
  const metrics = run?.dtu_metrics?.metrics;
  if (!isDtu() || !metrics) {
    panel.hidden = true;
    return;
  }
  panel.hidden = false;
  const accuracy = metrics.accuracy_data_to_stl;
  const completeness = metrics.completeness_stl_to_data;
  const alignment = run.dtu_metrics.alignment;
  const parts = [
    `Accuracy mean ${format(accuracy.mean_mm, 3)} mm · median ${format(accuracy.median_mm, 3)} mm`,
    `Completeness mean ${format(completeness.mean_mm, 3)} mm · median ${format(completeness.median_mm, 3)} mm`,
  ];
  if (alignment) {
    parts.push(
      `Alignement caméras : ${alignment.aligned_cameras} vues · erreur centres ${format(alignment.mean_camera_center_error_mm, 3)} mm`,
    );
  }
  $('dtu-summary').textContent = parts.join(' · ');
}

function coordinates() {
  const points = model?.points || [];
  if (!points.length) return { center: [0, 0, 0], scale: 1 };
  const coords = [0, 1, 2].map((axis) => points.map((point) => point.xyz[axis]).sort((a, b) => a - b));
  const center = coords.map((values) => values[Math.floor(values.length / 2)]);
  const extent = Math.max(
    ...coords.map((values) => values[Math.floor((values.length - 1) * 0.95)] - values[Math.floor((values.length - 1) * 0.05)]),
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
  if (!model) return;
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
    return { x: width / 2 + xx * factor, y: height / 2 - yy * factor, z: depth };
  };
  projected = model.points.map((point) => ({ ...project(point.xyz), point })).sort((a, b) => a.z - b.z);
  for (const item of projected) {
    ctx.fillStyle = `rgb(${item.point.color.join(',')})`;
    ctx.fillRect(item.x - 1.5, item.y - 1.5, 3, 3);
  }
  if ($('cameras').checked) {
    ctx.strokeStyle = '#6ab9ee';
    for (const camera of model.images) {
      const point = project(camera.center);
      ctx.strokeRect(point.x - 3, point.y - 3, 6, 6);
    }
  }
}

function loadPhoto() {
  const name = $('photo').value;
  const token = ++photoToken;
  photoEntry = model?.images.find((image) => image.name === name) || null;
  const source = run.images.find((image) => image.name === name);
  $('photo-status').textContent = photoEntry ? 'Raccordée' : 'Non raccordée';
  $('photo-detail').textContent = photoEntry
    ? `${photoEntry.observations.length} points 3D observés · caméra ${photoEntry.camera_id} · ${source.connected_views} vues liées`
    : `${source?.features || 0} caractéristiques · ${source?.connected_views || 0} vues liées · pose non estimée`;
  const image = new Image();
  image.onload = () => {
    if (token !== photoToken) return;
    photoImage = image;
    drawPhoto();
  };
  image.onerror = () => {
    if (token !== photoToken) return;
    photoImage = null;
    $('photo-detail').textContent = 'Image inaccessible. Ouvrir le visualiseur depuis le serveur local.';
    drawPhoto();
  };
  image.src = imageBase() + encodeURIComponent(name);
}

function drawPhoto() {
  const canvas = $('photo-canvas');
  const context = canvas.getContext('2d');
  if (!photoImage) {
    context.clearRect(0, 0, canvas.width, canvas.height);
    return;
  }
  canvas.width = photoImage.naturalWidth;
  canvas.height = photoImage.naturalHeight;
  context.drawImage(photoImage, 0, 0);
  const scaleX = canvas.width / (photoEntry?.width || canvas.width);
  const scaleY = canvas.height / (photoEntry?.height || canvas.height);
  for (const observation of photoEntry?.observations || []) {
    context.fillStyle = observation.point === selected ? '#ffb650' : '#4df0bb';
    context.beginPath();
    context.arc(observation.xy[0] * scaleX, observation.xy[1] * scaleY, observation.point === selected ? 13 : 4, 0, Math.PI * 2);
    context.fill();
  }
  drawAnnotations(context);
}

function selectPoint(id) {
  selected = id;
  const point = model.points.find((entry) => entry.id === id);
  if (!point) return;
  const views = model.images.filter((image) => image.observations.some((observation) => observation.point === id));
  $('selection').textContent = `Point ${id} · ${point.track_length} observations · erreur ${format(point.error, 3)} px. Vérifie qu’il correspond au même détail dans les photos.`;
  if (views.length && !views.some((image) => image.name === $('photo').value)) {
    $('photo').value = views[0].name;
    loadPhoto();
  } else {
    drawPhoto();
  }
  draw();
}

function annotationKey() {
  return `formascale:${data.capture}:${run.name}:${model?.id}:annotations:v1`;
}

function loadAnnotations() {
  annotations = { A: [], B: [], C: [], D: [] };
  try {
    const saved = JSON.parse(localStorage.getItem(annotationKey()) || 'null');
    if (saved && ['A', 'B', 'C', 'D'].every((key) => Array.isArray(saved[key]))) annotations = saved;
  } catch (_) {
    /* ignore corrupt local storage */
  }
  updateAnnotationStatus();
}

function updateAnnotationStatus() {
  if (!$('annotation-status')) return;
  $('annotation-status').textContent = ['A', 'B', 'C', 'D']
    .map((key) => `${key} : ${annotations[key].length} vues`)
    .join(' · ');
}

function saveAnnotations() {
  try {
    localStorage.setItem(annotationKey(), JSON.stringify(annotations));
  } catch (_) {
    $('annotation-status').textContent = 'Stockage navigateur indisponible : exporte les repères avant de fermer.';
    return;
  }
  updateAnnotationStatus();
}

function placeAnnotation(label, xy) {
  annotations[label] = annotations[label].filter((observation) => observation.image !== photoEntry.name);
  annotations[label].push({ image: photoEntry.name, xy: xy.map((value) => Math.round(value * 100) / 100) });
  saveAnnotations();
  drawPhoto();
}

function drawAnnotations(context) {
  const name = $('photo').value;
  for (const label of ['A', 'B', 'C', 'D']) {
    const observation = annotations[label].find((entry) => entry.image === name);
    if (!observation) continue;
    const [x, y] = observation.xy;
    context.strokeStyle = '#ffb650';
    context.fillStyle = '#ffb650';
    context.lineWidth = 3;
    context.beginPath();
    context.moveTo(x - 14, y);
    context.lineTo(x + 14, y);
    context.moveTo(x, y - 14);
    context.lineTo(x, y + 14);
    context.stroke();
    context.font = 'bold 30px Segoe UI';
    context.fillText(label, x + 18, y - 18);
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
  if (keys.has('w')) zoom = Math.min(12, zoom * 1.02);
  if (keys.has('s')) zoom = Math.max(0.1, zoom * 0.98);
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
  pitch += dy * 0.008;
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
  zoom = Math.min(12, Math.max(0.1, zoom * Math.exp(-event.deltaY * 0.001)));
  draw();
}, { passive: false });

window.addEventListener('keydown', (event) => {
  if (event.target && /input|select|textarea/i.test(event.target.tagName)) return;
  const key = event.key.toLowerCase();
  if (key === 'r') {
    yaw = 0.35;
    pitch = -0.45;
    zoom = 1;
    focus = [0, 0, 0];
    dist = 2.2;
    draw();
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

$('photo-canvas').addEventListener('click', (event) => {
  if (!photoEntry) return;
  const rect = event.target.getBoundingClientRect();
  const x = ((event.clientX - rect.left) / rect.width) * photoEntry.width;
  const y = ((event.clientY - rect.top) / rect.height) * photoEntry.height;
  if ($('action').value !== 'inspect') {
    placeAnnotation($('action').value, [x, y]);
    return;
  }
  let nearest = null;
  let distance = (18 / rect.width) * photoEntry.width;
  for (const observation of photoEntry.observations) {
    const delta = Math.hypot(observation.xy[0] - x, observation.xy[1] - y);
    if (delta < distance) {
      distance = delta;
      nearest = observation;
    }
  }
  if (nearest) selectPoint(nearest.point);
});

$('capture').addEventListener('change', loadCapture);
$('run').addEventListener('change', loadRun);
$('model').addEventListener('change', loadModel);
$('photo').addEventListener('change', loadPhoto);
$('cameras').addEventListener('change', draw);
$('reset').addEventListener('click', () => {
  yaw = 0.35;
  pitch = -0.45;
  zoom = 1;
  focus = [0, 0, 0];
  dist = 2.2;
  draw();
});
$('export').addEventListener('click', () => {
  const blob = new Blob([JSON.stringify({ capture: data.capture, run: run.name, summary: run.summary, dtu_metrics: run.dtu_metrics }, null, 2)], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = `${data.capture}-${run.name}-diagnostic.json`;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
});
$('remove-annotation').addEventListener('click', () => {
  const label = $('action').value;
  if (label === 'inspect') return;
  annotations[label] = annotations[label].filter((observation) => observation.image !== $('photo').value);
  saveAnnotations();
  drawPhoto();
});
$('export-annotations').addEventListener('click', () => {
  const length = Number($('reference-length').value);
  if (!(length > 0 && Number.isFinite(length))) {
    $('annotation-status').textContent = 'Indique une longueur positive en millimètres.';
    return;
  }
  if (annotations.A.length < 3 || annotations.B.length < 3) {
    $('annotation-status').textContent = 'Il faut au moins trois vues pour A et trois pour B.';
    return;
  }
  const hasMeasure = annotations.C.length || annotations.D.length;
  if (hasMeasure && (annotations.C.length < 3 || annotations.D.length < 3)) {
    $('annotation-status').textContent = 'Pour mesurer C–D, place aussi C et D dans trois vues chacun.';
    return;
  }
  const points = { A: annotations.A, B: annotations.B };
  if (hasMeasure) {
    points.C = annotations.C;
    points.D = annotations.D;
  }
  const result = {
    capture: data.capture,
    run: run.name,
    model_id: model.id,
    coordinate_space: 'prepared_images_pixels',
    reference: { a: 'A', b: 'B', length_mm: length },
    points,
    measurements: hasMeasure ? [{ a: 'C', b: 'D' }] : [],
  };
  const url = URL.createObjectURL(new Blob([JSON.stringify(result, null, 2)], { type: 'application/json' }));
  const link = document.createElement('a');
  link.href = url;
  link.download = `${data.capture}-${run.name}-annotations.json`;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
});
window.addEventListener('resize', draw);

if (!catalog.length) {
  $('status-badge').textContent = 'Aucun jeu exporté';
  $('selection').textContent = 'Lance scripts/export_diagnostics.py sur une capture, puis recharge.';
} else {
  for (let index = 0; index < catalog.length; index += 1) {
    $('capture').append(option(index, catalog[index].label));
  }
  const preferred = Math.max(0, catalog.findIndex((entry) => entry.id === 'dtu_scan001'));
  $('capture').value = String(preferred === -1 ? 0 : preferred);
  loadCapture();
}
