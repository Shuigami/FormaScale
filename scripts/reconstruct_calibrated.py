"""Metadata-aware reconstruction, with fresh immutable experiment directories.

Example: python scripts/reconstruct_calibrated.py captures/bose_01 --run metadata_01
EXIF focal lengths are approximate priors, not a measured calibration.
"""
import argparse
from collections import defaultdict
import hashlib
import json
import math
from pathlib import Path
import resource
import time

from PIL import Image, ImageOps
from pillow_heif import register_heif_opener
import pycolmap as p


def metadata_groups(capture):
    register_heif_opener()
    groups = defaultdict(list)
    manifest = []
    for source in sorted((capture / 'images').iterdir()):
        prepared = capture / 'prepared/images' / (source.name + '.jpg')
        if not prepared.is_file():
            continue
        with Image.open(source) as im:
            exif = im.getexif()
            photo = exif.get_ifd(34665)
            oriented = ImageOps.exif_transpose(im)
            original_size = oriented.size
            focal35 = float(photo.get(41989, 0))
            focal_mm = float(photo.get(37386, 0))
            zoom = float(photo.get(41988, 1))
            lens = str(photo.get(42036, 'unknown'))
        with Image.open(prepared) as im:
            width, height = im.size
        # Keep different sensor modes / original resolutions separate. Intrinsics
        # are shared only within identical lens, orientation, resolution and zoom.
        key = (lens, original_size, width, height, focal35, round(zoom, 2))
        record = {'source': source.name, 'prepared': prepared.name,
                  'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
                  'prepared_sha256': hashlib.sha256(prepared.read_bytes()).hexdigest(),
                  'original_size': original_size, 'prepared_size': [width,height],
                  'lens': lens, 'focal_mm': focal_mm, 'focal35_mm': focal35,
                  'digital_zoom': zoom}
        groups[key].append(record)
        manifest.append(record)
    return groups, manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('capture', type=Path)
    parser.add_argument('--run', required=True)
    parser.add_argument('--threads', type=int, default=2)
    parser.add_argument('--features', type=int, default=4096)
    parser.add_argument('--guided', action='store_true')
    args = parser.parse_args()
    if Path(args.run).name != args.run or args.run in {'.','..'}:
        parser.error('--run must be a single directory name')
    if args.threads < 1 or args.features < 1:
        parser.error('threads and features must be positive')
    root = args.capture.resolve()
    groups, manifest = metadata_groups(root)
    if len(manifest) < 3:
        parser.error('at least three prepared images are required')
    output = root / 'runs' / args.run
    output.mkdir(parents=True, exist_ok=False)
    status_path = output / 'status.json'
    def status(stage, **extra):
        status_path.write_text(json.dumps({'stage': stage, **extra}, indent=2))
        print(stage, flush=True)
    database = output / 'database.db'
    images = root / 'prepared/images'
    extraction = p.FeatureExtractionOptions()
    extraction.num_threads = args.threads
    extraction.max_image_size = 2000
    extraction.sift.max_num_features = args.features
    matching = p.FeatureMatchingOptions()
    matching.num_threads = args.threads
    matching.guided_matching = args.guided
    mapping = p.IncrementalPipelineOptions()
    mapping.num_threads = args.threads
    mapping.random_seed = 0
    mapping.ba_min_num_residuals_for_cpu_multi_threading = 1000000
    mapping.max_runtime_seconds = 300
    config = {'pycolmap': p.__version__, 'device': 'cpu',
              'camera_prior': '35mm-equivalent diagonal focal estimate; refined during mapping',
              'grouping': 'lens + native oriented resolution + prepared resolution + focal35 + rounded zoom',
              'manifest': manifest, 'extraction': extraction.todict(),
              'matching': matching.todict(), 'mapping': mapping.todict()}
    (output / 'config.json').write_text(json.dumps(config, default=str, indent=2))
    timings = {}
    start = time.perf_counter()
    try:
        p.logging.minloglevel = 1
        status('extracting', images=len(manifest), camera_groups=len(groups))
        for group in groups.values():
            first = group[0]
            width, height = first['prepared_size']
            focal = first['focal35_mm'] / math.hypot(36,24) * math.hypot(width,height) if first['focal35_mm'] else 1.2*max(width,height)
            camera = p.Camera.create_from_model_name(0, 'SIMPLE_RADIAL', focal, width, height)
            camera.has_prior_focal_length = first['focal35_mm'] > 0
            with p.Database.open(database) as db:
                camera_id = db.write_camera(camera)
            reader = p.ImageReaderOptions()
            reader.existing_camera_id = camera_id
            p.extract_features(database, images, image_names=[r['prepared'] for r in group],
                               reader_options=reader, extraction_options=extraction, device=p.Device.cpu)
        extracted = time.perf_counter()
        timings['extraction'] = extracted-start
        status('matching')
        p.match_exhaustive(database, matching_options=matching, device=p.Device.cpu)
        matched = time.perf_counter()
        timings['matching'] = matched-extracted
        status('mapping')
        sparse = output / 'sparse'
        sparse.mkdir()
        models = p.incremental_mapping(database, images, sparse, options=mapping)
        finished = time.perf_counter()
        timings.update(mapping=finished-matched, total=finished-start)
        results = []
        for index, model in models.items():
            registered = model.reg_image_ids()
            directory = sparse / str(index)
            directory.mkdir(exist_ok=True)
            model.write(directory)
            model.export_PLY(str(directory / 'points.ply'))
            results.append({'id': index, 'registered_images': len(registered),
                'points3D': model.num_points3D(), 'mean_reprojection_error_px': model.compute_mean_reprojection_error(),
                'image_names': sorted(model.images[i].name for i in registered)})
        summary = {'input_images': len(manifest), 'camera_groups': len(groups), 'models': results,
            'seconds': timings, 'peak_process_ram_mib': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024,
            'metric_scale_validated': False, 'object_geometry_validated': False,
            'note': 'Scene reconstruction may include background. No metric accuracy claim.'}
        (output / 'summary.json').write_text(json.dumps(summary, indent=2))
        status('complete', models=len(models))
        print(json.dumps({**summary,'models':[{k:v for k,v in r.items() if k!='image_names'} for r in results]},indent=2))
    except BaseException as exc:
        status('failed', error=str(exc), elapsed_seconds=time.perf_counter()-start)
        raise

if __name__ == '__main__':
    main()

