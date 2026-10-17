"""Incremental sparse SfM for live sessions. Requires pycolmap (WSL venv)."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from formascale.ply import read_ply_points  # noqa: E402


def _thumb(path: Path, size=(64, 36)) -> np.ndarray:
    with Image.open(path) as im:
        gray = im.convert('L').resize(size, Image.Resampling.BILINEAR)
        return np.asarray(gray, dtype=np.int16)


def _rot_a_to_b(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Rotation matrix sending unit vector a to unit vector b."""
    a = a / (np.linalg.norm(a) + 1e-12)
    b = b / (np.linalg.norm(b) + 1e-12)
    cross = np.cross(a, b)
    dot = float(np.dot(a, b))
    if dot < -0.999999:
        axis = np.array([1.0, 0.0, 0.0]) if abs(a[0]) < 0.9 else np.array([0.0, 1.0, 0.0])
        axis = np.cross(a, axis)
        axis = axis / (np.linalg.norm(axis) + 1e-12)
        return -np.eye(3) + 2 * np.outer(axis, axis)
    skew = np.array([
        [0.0, -cross[2], cross[1]],
        [cross[2], 0.0, -cross[0]],
        [-cross[1], cross[0], 0.0],
    ])
    return np.eye(3) + skew + skew @ skew * (1.0 / (1.0 + dot))


def display_alignment_from_model(model) -> np.ndarray:
    """Map average phone 'up' to +Y and average view into +Z for the live viewer."""
    ups = []
    views = []
    for image_id in model.reg_image_ids():
        image = model.images[image_id]
        has_pose = getattr(image, 'has_pose', True)
        if callable(has_pose):
            has_pose = has_pose()
        if not has_pose:
            continue
        cfw = image.cam_from_world
        if callable(cfw):
            cfw = cfw()
        rotation = np.asarray(cfw.rotation.matrix(), dtype=float)
        # COLMAP camera: X right, Y down, Z forward → image-up is -Y.
        ups.append(rotation.T @ np.array([0.0, -1.0, 0.0]))
        views.append(np.asarray(image.viewing_direction(), dtype=float))
    if not ups:
        return np.eye(3)

    up = np.mean(ups, axis=0)
    r_up = _rot_a_to_b(up, np.array([0.0, 1.0, 0.0]))
    view = r_up @ np.mean(views, axis=0)
    view[1] = 0.0
    norm = np.linalg.norm(view)
    if norm < 1e-6:
        return r_up
    view = view / norm
    # Yaw only: face the average viewing direction toward +Z, keep phone-up as +Y.
    angle = np.arctan2(view[0], view[2])
    c, s = np.cos(-angle), np.sin(-angle)
    r_yaw = np.array([[c, 0.0, s], [0.0, 1.0, 0.0], [-s, 0.0, c]])
    return r_yaw @ r_up


def apply_rotation(xyz: list[float], rotation: np.ndarray) -> list[float]:
    out = rotation @ np.asarray(xyz, dtype=float)
    return [float(out[0]), float(out[1]), float(out[2])]


def select_diverse_keyframes(images: list[Path], max_keep: int = 48, min_diff: float = 12.0) -> list[Path]:
    """Keep a short orbit of distinct views. Near-duplicates kill SfM baselines."""
    if len(images) <= max_keep:
        # Still drop near-duplicates even under the cap.
        selected = [images[0]]
        last = _thumb(images[0])
        for path in images[1:]:
            thumb = _thumb(path)
            if float(np.mean(np.abs(thumb - last))) >= min_diff:
                selected.append(path)
                last = thumb
        if selected[-1] != images[-1]:
            selected.append(images[-1])
        return selected

    # Spread candidates across the sequence, then enforce diversity.
    step = max(1, len(images) // (max_keep * 2))
    candidates = images[::step]
    if candidates[-1] != images[-1]:
        candidates.append(images[-1])

    selected = [candidates[0]]
    last = _thumb(candidates[0])
    for path in candidates[1:]:
        if len(selected) >= max_keep:
            break
        thumb = _thumb(path)
        if float(np.mean(np.abs(thumb - last))) >= min_diff:
            selected.append(path)
            last = thumb
    if selected[-1] != images[-1] and len(selected) < max_keep:
        selected.append(images[-1])
    elif selected[-1] != images[-1]:
        selected[-1] = images[-1]
    return selected


class LiveSfmWorker:
    """Rebuilds a sparse model when new keyframes arrive and the worker is idle."""

    def __init__(self, session_dir: Path, on_update, threads: int = 2):
        self.session_dir = session_dir
        self.images_dir = session_dir / 'prepared' / 'images'
        self.subset_dir = session_dir / 'prepared' / 'sfm_subset'
        self.run_dir = session_dir / 'runs' / 'live'
        self.images_dir.mkdir(parents=True, exist_ok=True)
        self.on_update = on_update
        self.threads = threads
        self._lock = threading.Lock()
        self._pending = False
        self._busy = False
        self._dense_pending = False
        self._force = False
        self._discard = False
        self._last_keyframe_at = 0.0
        self._last_run_count = 0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name='live-sfm', daemon=True)
        self._display_align = np.eye(3)
        self.status = {
            'state': 'idle',
            'keyframes': 0,
            'used': 0,
            'points': 0,
            'registered': 0,
            'message': 'En attente de keyframes',
            'error': None,
            'seconds': None,
            'mode': 'sparse',
        }

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop.set()

    def notify_new_keyframe(self, force: bool = False):
        with self._lock:
            self._pending = True
            self._discard = False
            if force:
                self._force = True
            self._last_keyframe_at = time.time()
            self.status['keyframes'] = len(list(self.images_dir.glob('*.jpg')))

    def request_dense(self) -> dict:
        """Queue dense MVS (or reload fused.ply if already present)."""
        fused = self.run_dir / 'dense' / 'fused.ply'
        sparse_ok = (self.run_dir / 'sparse').is_dir()
        if not sparse_ok and not fused.is_file():
            return {'ok': False, 'message': 'Sparse requis avant densification'}
        with self._lock:
            self._dense_pending = True
            self._discard = False
        return {
            'ok': True,
            'message': 'Chargement du dense…' if fused.is_file() else 'Densification GPU demandée…',
            'cached': fused.is_file(),
        }

    def clear_keyframes(self) -> int:
        """Delete all saved keyframes and reset live SfM state."""
        with self._lock:
            self._pending = False
            self._dense_pending = False
            self._force = False
            self._discard = True
            self._last_run_count = 0
            self._last_keyframe_at = 0.0
        removed = 0
        for path in list(self.images_dir.glob('*.jpg')):
            path.unlink(missing_ok=True)
            removed += 1
        if self.subset_dir.exists():
            shutil.rmtree(self.subset_dir, ignore_errors=True)
        if self.run_dir.exists():
            shutil.rmtree(self.run_dir, ignore_errors=True)
        self.images_dir.mkdir(parents=True, exist_ok=True)
        self._display_align = np.eye(3)
        self.status.update({
            'state': 'idle',
            'keyframes': 0,
            'used': 0,
            'points': 0,
            'registered': 0,
            'message': 'Keyframes effacées — nouvelle observation',
            'error': None,
            'seconds': None,
            'mode': 'sparse',
        })
        self.on_update(self._empty_payload())
        return removed

    def _loop(self):
        while not self._stop.is_set():
            now = time.time()
            with self._lock:
                count = len(list(self.images_dir.glob('*.jpg')))
                idle_enough = (now - self._last_keyframe_at) >= 2.5
                grew = count >= self._last_run_count + 4
                should_run = (
                    self._pending
                    and not self._busy
                    and count >= 3
                    and (self._force or (idle_enough and grew) or (idle_enough and self._last_run_count == 0))
                )
                should_dense = self._dense_pending and not self._busy
                if should_run:
                    self._pending = False
                    self._force = False
                    self._busy = True
                    self._last_run_count = count
                elif should_dense:
                    self._dense_pending = False
                    self._busy = True
            if should_run:
                try:
                    self._reconstruct()
                except Exception as exc:  # noqa: BLE001 — surface to UI
                    with self._lock:
                        discarded = self._discard
                        self._discard = False
                    if not discarded:
                        self.status.update({
                            'state': 'error',
                            'mode': 'sparse',
                            'message': 'Échec de la reconstruction',
                            'error': str(exc),
                        })
                        self.on_update(self._empty_payload())
                finally:
                    with self._lock:
                        self._busy = False
            elif should_dense:
                try:
                    self._densify()
                except Exception as exc:  # noqa: BLE001
                    with self._lock:
                        discarded = self._discard
                        self._discard = False
                    if not discarded:
                        self.status.update({
                            'state': 'error',
                            'mode': 'dense',
                            'message': 'Échec densification',
                            'error': str(exc),
                        })
                        self.on_update(self._empty_payload())
                finally:
                    with self._lock:
                        self._busy = False
            else:
                self._stop.wait(0.4)

    def _prepare_subset(self, images: list[Path]) -> list[Path]:
        selected = select_diverse_keyframes(images, max_keep=48, min_diff=12.0)
        if self.subset_dir.exists():
            shutil.rmtree(self.subset_dir)
        self.subset_dir.mkdir(parents=True)
        linked = []
        for index, path in enumerate(selected, start=1):
            dest = self.subset_dir / f'{index:04d}_{path.name}'
            shutil.copy2(path, dest)
            linked.append(dest)
        return linked

    def _reconstruct(self):
        images = sorted(self.images_dir.glob('*.jpg'))
        count = len(images)
        self.status.update({
            'state': 'running',
            'keyframes': count,
            'message': f'Sélection des vues parmi {count} keyframes…',
            'error': None,
        })
        self.on_update(self._empty_payload())

        if count < 3:
            self.status.update({
                'state': 'idle',
                'message': f'Au moins 3 keyframes nécessaires ({count}/3)',
            })
            self.on_update(self._empty_payload())
            return

        try:
            import pycolmap as p
        except ImportError as exc:
            raise RuntimeError(
                'pycolmap introuvable. Lance le serveur depuis le venv FormaScale (WSL).'
            ) from exc

        subset = self._prepare_subset(images)
        used = len(subset)
        self.status.update({
            'used': used,
            'message': f'Reconstruction sur {used} vues distinctes (sur {count})…',
        })
        self.on_update(self._empty_payload())

        if used < 3:
            self.status.update({
                'state': 'idle',
                'used': used,
                'message': 'Vues trop similaires — bouge davantage autour de l’objet',
            })
            self.on_update(self._empty_payload())
            return

        self.run_dir.mkdir(parents=True, exist_ok=True)
        # Keep dense/ across sparse rebuilds so Densifier can reuse fused.ply.
        for name in ('database.db', 'summary.json', 'display_align.json'):
            (self.run_dir / name).unlink(missing_ok=True)
        sparse = self.run_dir / 'sparse'
        if sparse.exists():
            shutil.rmtree(sparse)
        sparse.mkdir()
        db = self.run_dir / 'database.db'

        p.logging.minloglevel = 2
        extraction = p.FeatureExtractionOptions()
        extraction.num_threads = self.threads
        extraction.max_image_size = 1600
        extraction.sift.max_num_features = 4096
        matching = p.FeatureMatchingOptions()
        matching.num_threads = self.threads
        matching.guided_matching = True
        mapping = p.IncrementalPipelineOptions()
        mapping.num_threads = self.threads
        mapping.random_seed = 0
        mapping.min_num_matches = 12
        # Live phone orbits often have modest parallax at first.
        mapping.mapper.init_min_tri_angle = 8.0
        mapping.mapper.init_min_num_inliers = 60
        mapping.mapper.abs_pose_min_num_inliers = 20
        mapping.mapper.abs_pose_min_inlier_ratio = 0.18
        mapping.mapper.filter_min_tri_angle = 1.0
        mapping.mapper.max_reg_trials = 5

        started = time.perf_counter()
        p.extract_features(
            db, self.subset_dir,
            camera_mode=p.CameraMode.SINGLE,
            extraction_options=extraction,
            device=p.Device.cpu,
        )
        # Exhaustive on the thinned set; sequential is too fragile with near-dupes.
        if used <= 60:
            p.match_exhaustive(db, matching_options=matching, device=p.Device.cpu)
        else:
            pairing = p.SequentialPairingOptions()
            pairing.overlap = 15
            pairing.quadratic_overlap = True
            p.match_sequential(
                db,
                matching_options=matching,
                pairing_options=pairing,
                device=p.Device.cpu,
            )
        models = p.incremental_mapping(db, self.subset_dir, sparse, options=mapping)
        elapsed = time.perf_counter() - started

        best = None
        best_id = None
        for index, model in models.items():
            if best is None or model.num_reg_images() > best.num_reg_images():
                best = model
                best_id = index
            elif (
                best is not None
                and model.num_reg_images() == best.num_reg_images()
                and model.num_points3D() > best.num_points3D()
            ):
                best = model
                best_id = index
        if best is None or best.num_points3D() == 0:
            self.status.update({
                'state': 'idle',
                'points': 0,
                'registered': 0,
                'used': used,
                'seconds': round(elapsed, 2),
                'message': 'Aucune géométrie solide — oriente plus lentement, angles plus larges',
            })
            self.on_update(self._empty_payload())
            return

        points = []
        for point_id, point in best.points3D.items():
            raw = getattr(point, 'color', None)
            if raw is None:
                color = [180, 200, 210]
            else:
                color = [int(c) for c in list(raw)[:3]]
            track_length = point.track.length() if hasattr(point, 'track') else 0
            points.append({
                'id': int(point_id),
                'xyz': [float(point.xyz[0]), float(point.xyz[1]), float(point.xyz[2])],
                'color': color,
                'error': float(point.error) if hasattr(point, 'error') else 0.0,
                'track_length': int(track_length),
            })
        # Prefer well-tracked points for the interactive inspector.
        points.sort(key=lambda item: (-item['track_length'], item['error'], item['id']))
        if len(points) > 6000:
            points = points[:6000]
        kept = {point['id'] for point in points}

        images = []
        for image_id in best.reg_image_ids():
            image = best.images[image_id]
            camera = best.cameras[image.camera_id]
            try:
                center = image.projection_center()
            except Exception:  # noqa: BLE001
                continue
            observations = []
            for obs in image.points2D:
                if not obs.has_point3D():
                    continue
                point_id = int(obs.point3D_id)
                if point_id not in kept:
                    continue
                observations.append({
                    'xy': [float(obs.xy[0]), float(obs.xy[1])],
                    'point': point_id,
                })
            images.append({
                'id': int(image_id),
                'name': image.name,
                'center': [float(center[0]), float(center[1]), float(center[2])],
                'width': int(camera.width),
                'height': int(camera.height),
                'observations': observations,
            })
        images.sort(key=lambda item: item['name'])

        # COLMAP world frame is arbitrary; align so phone-up ≈ screen-up.
        align = display_alignment_from_model(best)
        self._display_align = align
        (self.run_dir / 'display_align.json').write_text(
            json.dumps(align.tolist()), encoding='utf-8',
        )
        for point in points:
            point['xyz'] = apply_rotation(point['xyz'], align)
        for image in images:
            image['center'] = apply_rotation(image['center'], align)
        cameras = [{'name': image['name'], 'center': image['center']} for image in images]

        summary = {
            'keyframes': count,
            'used_for_sfm': used,
            'registered': best.num_reg_images(),
            'points3D': best.num_points3D(),
            'mean_reprojection_error_px': best.compute_mean_reprojection_error(),
            'seconds': round(elapsed, 2),
            'model_id': int(best_id) if best_id is not None else 0,
        }
        (self.run_dir / 'summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
        best.export_PLY(str(sparse / str(best_id) / 'points.ply'))

        with self._lock:
            discarded = self._discard
            self._discard = False
        if discarded:
            return

        self.status.update({
            'state': 'ready',
            'mode': 'sparse',
            'keyframes': count,
            'used': used,
            'points': len(points),
            'registered': summary['registered'],
            'seconds': summary['seconds'],
            'message': (
                f'{summary["registered"]}/{used} vues · {summary["points3D"]} points · '
                f'{summary["seconds"]} s (sur {count} keyframes brutes)'
            ),
            'error': None,
        })
        self.on_update(self._payload(points=points, cameras=cameras, images=images))

    def _load_display_align(self) -> np.ndarray:
        path = self.run_dir / 'display_align.json'
        if path.is_file():
            try:
                return np.asarray(json.loads(path.read_text(encoding='utf-8')), dtype=float)
            except (json.JSONDecodeError, ValueError, TypeError):
                pass
        return self._display_align if self._display_align is not None else np.eye(3)

    def _densify(self):
        fused = self.run_dir / 'dense' / 'fused.ply'
        self.status.update({
            'state': 'running',
            'mode': 'dense',
            'message': 'Chargement du nuage dense…' if fused.is_file() else 'Densification GPU (PatchMatch)…',
            'error': None,
        })
        self.on_update(self._empty_payload())

        if not fused.is_file():
            script = ROOT / 'scripts' / 'reconstruct_dense.py'
            started = time.perf_counter()
            proc = subprocess.run(
                [
                    sys.executable,
                    str(script),
                    str(self.session_dir),
                    '--run', self.run_dir.name,
                    '--max-image-size', '1000',
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            if proc.returncode != 0 or not fused.is_file():
                detail = (proc.stderr or proc.stdout or '').strip()[-800:]
                raise RuntimeError(detail or f'reconstruct_dense exit {proc.returncode}')
            dense_seconds = round(time.perf_counter() - started, 2)
        else:
            dense_seconds = None

        with self._lock:
            discarded = self._discard
            self._discard = False
        if discarded:
            return

        points_raw, total = read_ply_points(fused, limit=12000)
        align = self._load_display_align()
        points = []
        for point in points_raw:
            points.append({
                **point,
                'xyz': apply_rotation(point['xyz'], align),
            })

        cameras = []
        images = []
        sparse_summary = {}
        summary_path = self.run_dir / 'summary.json'
        if summary_path.is_file():
            sparse_summary = json.loads(summary_path.read_text(encoding='utf-8'))
        model_id = sparse_summary.get('model_id', 0)
        sparse_model = self.run_dir / 'sparse' / str(model_id)
        if sparse_model.is_dir():
            try:
                import pycolmap as p
                model = p.Reconstruction(sparse_model)
                for image_id in model.reg_image_ids():
                    image = model.images[image_id]
                    try:
                        center = image.projection_center()
                    except Exception:  # noqa: BLE001
                        continue
                    center = apply_rotation(
                        [float(center[0]), float(center[1]), float(center[2])],
                        align,
                    )
                    cameras.append({'name': image.name, 'center': center})
            except Exception:  # noqa: BLE001 — cameras optional for dense view
                cameras = []

        self.status.update({
            'state': 'ready',
            'mode': 'dense',
            'points': len(points),
            'registered': sparse_summary.get('registered', 0),
            'used': sparse_summary.get('used_for_sfm', 0),
            'keyframes': sparse_summary.get('keyframes', self.status.get('keyframes', 0)),
            'seconds': dense_seconds,
            'message': (
                f'Dense · {len(points)}/{total} pts affichés'
                + (f' · {dense_seconds} s' if dense_seconds is not None else ' (cache)')
            ),
            'error': None,
        })
        self.on_update(self._payload(points=points, cameras=cameras, images=images))

    def _empty_payload(self):
        return self._payload(points=[], cameras=[], images=[])

    def _payload(self, points, cameras, images):
        return {
            'type': 'reconstruction',
            'status': dict(self.status),
            'points': points,
            'cameras': cameras,
            'images': images,
        }
