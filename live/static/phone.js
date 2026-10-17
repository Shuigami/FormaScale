'use strict';

const $ = (id) => document.getElementById(id);
const video = $('local-video');
const canvas = document.createElement('canvas');
const ctx = canvas.getContext('2d', { alpha: false });

let stream = null;
let track = null;
let previewTimer = null;
let autoTimer = null;
let autoOn = false;
let sending = false;
let keyframes = 0;
let lastHash = null;
let lastKeyframeAt = 0;
let autoIntervalMs = 2200;
let applyingRemote = false;
const MIN_AUTO_DIFF = 42;

function formatInterval(ms) {
  return `${(ms / 1000).toLocaleString('fr-FR', { maximumFractionDigits: 1 })} s`;
}

function setBadge(text, ok) {
  $('badge').textContent = text;
  $('badge').style.color = ok ? '#8dffd7' : '#f2c57c';
  $('badge').style.borderColor = ok ? '#3e7a72' : '#675336';
}

function setMeta(text) {
  $('meta').textContent = text;
}

function applyInterval(ms, { push = false } = {}) {
  const value = Math.max(500, Math.min(8000, Math.round(Number(ms) || 2200)));
  autoIntervalMs = value;
  applyingRemote = true;
  $('auto-interval').value = String(value);
  $('interval-label').textContent = formatInterval(value);
  applyingRemote = false;
  if (autoOn) {
    setMeta(
      `Auto : change d’angle · max 1 vue / ${formatInterval(value)}.`,
    );
  }
  if (push) {
    fetch('/api/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ auto_interval_ms: value }),
    }).catch(() => {});
  }
}

async function startCamera() {
  if (stream) return;
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      audio: false,
      video: {
        facingMode: { ideal: 'environment' },
        width: { ideal: 1280 },
        height: { ideal: 720 },
      },
    });
  } catch (err) {
    setBadge('Caméra refusée');
    setMeta(String(err.message || err));
    return;
  }
  video.srcObject = stream;
  await video.play();
  track = stream.getVideoTracks()[0] || null;
  $('start').disabled = true;
  $('keyframe').disabled = false;
  $('auto').disabled = false;
  $('torch').disabled = !(track && track.getCapabilities && track.getCapabilities().torch);
  setBadge('En direct', true);
  setMeta('Flux envoyé au PC. Ajoute des keyframes en tournant autour de l’objet.');
  previewTimer = setInterval(sendPreview, 180);
}

function frameBlob(quality, maxSide) {
  const vw = video.videoWidth;
  const vh = video.videoHeight;
  if (!vw || !vh) return null;
  const scale = Math.min(1, maxSide / Math.max(vw, vh));
  const w = Math.max(2, Math.round(vw * scale));
  const h = Math.max(2, Math.round(vh * scale));
  canvas.width = w;
  canvas.height = h;
  ctx.drawImage(video, 0, 0, w, h);
  return new Promise((resolve) => canvas.toBlob(resolve, 'image/jpeg', quality));
}

function roughHash() {
  const vw = video.videoWidth;
  const vh = video.videoHeight;
  if (!vw || !vh) return null;
  const w = 32;
  const h = 18;
  canvas.width = w;
  canvas.height = h;
  ctx.drawImage(video, 0, 0, w, h);
  const data = ctx.getImageData(0, 0, w, h).data;
  let hash = '';
  for (let i = 0; i < data.length; i += 16) {
    hash += Math.floor((data[i] + data[i + 1] + data[i + 2]) / 48).toString(16);
  }
  return hash;
}

function changedEnough(hash) {
  if (!hash) return false;
  const now = Date.now();
  if (now - lastKeyframeAt < autoIntervalMs) return false;
  if (!lastHash) {
    lastHash = hash;
    return true;
  }
  let diff = 0;
  const n = Math.min(hash.length, lastHash.length);
  for (let i = 0; i < n; i += 1) {
    if (hash[i] !== lastHash[i]) diff += 1;
  }
  // Require a clear viewpoint change — near-duplicates break SfM.
  if (diff >= MIN_AUTO_DIFF) {
    lastHash = hash;
    return true;
  }
  return false;
}

async function postBlob(url, blob) {
  const response = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'image/jpeg' },
    body: blob,
  });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return response.json();
}

async function sendPreview() {
  if (sending || video.readyState < 2) return;
  sending = true;
  try {
    const blob = await frameBlob(0.55, 720);
    if (blob) await postBlob('/api/preview', blob);
    setBadge('En direct', true);
  } catch (err) {
    setBadge('Connexion…');
    setMeta(`Envoi preview : ${err.message || err}`);
  } finally {
    sending = false;
  }
}

async function sendKeyframe(manual) {
  if (video.readyState < 2) return;
  try {
    const blob = await frameBlob(0.92, 1600);
    if (!blob) return;
    const result = await postBlob('/api/keyframe', blob);
    keyframes = result.keyframes || keyframes + 1;
    lastKeyframeAt = Date.now();
    setMeta(
      manual
        ? `Keyframe ${keyframes} envoyé.`
        : `Auto keyframe ${keyframes} — continue à tourner autour de l’objet.`,
    );
  } catch (err) {
    setMeta(`Keyframe échoué : ${err.message || err}`);
  }
}

function toggleAuto() {
  autoOn = !autoOn;
  $('auto').textContent = `Auto keyframes : ${autoOn ? 'on' : 'off'}`;
  if (autoTimer) {
    clearInterval(autoTimer);
    autoTimer = null;
  }
  if (autoOn) {
    lastHash = null;
    setMeta(
      `Auto : change d’angle · max 1 vue / ${formatInterval(autoIntervalMs)}.`,
    );
    autoTimer = setInterval(() => {
      if (changedEnough(roughHash())) sendKeyframe(false);
    }, 700);
  }
}

async function toggleTorch() {
  if (!track || !track.applyConstraints) return;
  const caps = track.getCapabilities ? track.getCapabilities() : {};
  if (!caps.torch) return;
  const settings = track.getSettings ? track.getSettings() : {};
  const next = !settings.torch;
  try {
    await track.applyConstraints({ advanced: [{ torch: next }] });
    $('torch').textContent = next ? 'Lampe on' : 'Lampe';
  } catch (err) {
    setMeta(`Lampe : ${err.message || err}`);
  }
}

async function clearKeyframes() {
  if (!window.confirm('Effacer toutes les keyframes et recommencer ?')) return;
  try {
    const response = await fetch('/api/clear', { method: 'POST' });
    const result = await response.json();
    keyframes = 0;
    lastHash = null;
    lastKeyframeAt = 0;
    setMeta(
      result.removed
        ? `${result.removed} keyframes effacées. Tu peux recommencer l’observation.`
        : 'Aucune keyframe à supprimer.',
    );
    setBadge('Prêt', true);
  } catch (err) {
    setMeta(`Effacement échoué : ${err.message || err}`);
  }
}

function connectSettings() {
  const source = new EventSource('/api/events');
  source.onmessage = (message) => {
    let event;
    try {
      event = JSON.parse(message.data);
    } catch (_) {
      return;
    }
    if (event.type === 'meta' || event.type === 'settings') {
      if (event.auto_interval_ms != null) applyInterval(event.auto_interval_ms);
    }
  };
}

$('start').addEventListener('click', startCamera);
$('keyframe').addEventListener('click', () => sendKeyframe(true));
$('auto').addEventListener('click', toggleAuto);
$('torch').addEventListener('click', toggleTorch);
$('clear-keyframes').addEventListener('click', clearKeyframes);
$('auto-interval').addEventListener('input', () => {
  if (applyingRemote) return;
  applyInterval($('auto-interval').value, { push: true });
});

fetch('/api/state')
  .then((response) => response.json())
  .then((state) => {
    if (state.auto_interval_ms != null) applyInterval(state.auto_interval_ms);
  })
  .catch(() => {})
  .finally(connectSettings);

if (!window.isSecureContext) {
  setBadge('HTTPS requis');
  setMeta('Safari iPhone refuse la caméra hors contexte sécurisé. Relance le serveur sans --http.');
}
