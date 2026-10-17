"""First SIFT/SfM baseline; this produces an unscaled sparse scene, not a mesh.
Run from project root: python scripts/reconstruct_sparse.py captures/bose_01
Each experiment must use a fresh output directory, avoiding stale databases.
"""
import argparse
import json
import resource
import time
from pathlib import Path
import pycolmap as p

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('capture', type=Path)
parser.add_argument('--run', required=True)
parser.add_argument('--threads', type=int, default=2)
args = parser.parse_args()
root = args.capture.resolve()
output = root / 'runs' / args.run
output.mkdir(parents=True, exist_ok=False)
images = root / 'prepared' / 'images'
db = output / 'database.db'
p.logging.minloglevel = 1
extraction = p.FeatureExtractionOptions()
extraction.num_threads = args.threads
extraction.max_image_size = 2000
extraction.sift.max_num_features = 4096
matching = p.FeatureMatchingOptions()
matching.num_threads = args.threads
mapping = p.IncrementalPipelineOptions()
mapping.num_threads = args.threads
mapping.random_seed = 0
mapping.max_runtime_seconds = 240
config = {'pycolmap': p.__version__, 'device': 'cpu', 'camera_mode': 'PER_IMAGE',
    'reason': 'Allow independent intrinsics for varying image orientation/resolution; not a calibrated camera.',
    'extraction': extraction.todict(), 'matching': matching.todict(), 'mapping': mapping.todict()}
(output / 'config.json').write_text(json.dumps(config, default=str, indent=2), encoding='utf-8')
started = time.perf_counter()
p.extract_features(db, images, camera_mode=p.CameraMode.PER_IMAGE, extraction_options=extraction, device=p.Device.cpu)
extracted = time.perf_counter()
print('Features extracted', flush=True)
p.match_exhaustive(db, matching_options=matching, device=p.Device.cpu)
matched = time.perf_counter()
print('Matching complete', flush=True)
sparse = output / 'sparse'
sparse.mkdir()
models = p.incremental_mapping(db, images, sparse, options=mapping)
finished = time.perf_counter()
summary = {'input_images': len(list(images.glob('*.jpg'))), 'models': [],
    'seconds': {'extraction': extracted-started, 'matching': matched-extracted, 'mapping': finished-matched, 'total': finished-started},
    'peak_process_ram_mib': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024,
    'metric_scale_validated': False, 'object_geometry_validated': False,
    'note': 'Sparse scene can include desk/background. Registration and reprojection do not validate object measurements.'}
for index, model in models.items():
    model.export_PLY(str(sparse / str(index) / 'points.ply'))
    summary['models'].append({'id': index, 'registered_images': model.num_reg_images(),
        'points3D': model.num_points3D(), 'mean_reprojection_error_px': model.compute_mean_reprojection_error(),
        'image_names': sorted(image.name for image in model.images.values())})
(output / 'summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
print(json.dumps(summary, indent=2), flush=True)

