"""Sparse SfM on a prepared DTU capture with a shared pinhole camera.

Example:
  python scripts/reconstruct_dtu.py captures/dtu_scan001 --run sift_shared_01
DTU SampleSet images are already rectified; this is not a calibrated-pose MVS run.
"""
from __future__ import annotations

import argparse
import json
import resource
import time
from pathlib import Path

import pycolmap as p
from PIL import Image


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture", type=Path)
    parser.add_argument("--run", required=True)
    parser.add_argument("--threads", type=int, default=2)
    parser.add_argument("--features", type=int, default=8192)
    parser.add_argument("--guided", action="store_true")
    args = parser.parse_args()
    if Path(args.run).name != args.run or args.run in {".", ".."}:
        parser.error("--run must be a single directory name")

    root = args.capture.resolve()
    images = root / "prepared" / "images"
    prepared = sorted(images.glob("*.jpg"))
    if len(prepared) < 3:
        raise SystemExit("need at least three prepared JPEG images")

    output = root / "runs" / args.run
    output.mkdir(parents=True, exist_ok=False)
    status_path = output / "status.json"

    def status(stage: str, **extra) -> None:
        status_path.write_text(json.dumps({"stage": stage, **extra}, indent=2))
        print(stage, flush=True)

    with Image.open(prepared[0]) as im:
        width, height = im.size
    focal = 1.2 * max(width, height)

    extraction = p.FeatureExtractionOptions()
    extraction.num_threads = args.threads
    extraction.max_image_size = max(width, height)
    extraction.sift.max_num_features = args.features
    matching = p.FeatureMatchingOptions()
    matching.num_threads = args.threads
    matching.guided_matching = args.guided
    mapping = p.IncrementalPipelineOptions()
    mapping.num_threads = args.threads
    mapping.random_seed = 0
    mapping.ba_min_num_residuals_for_cpu_multi_threading = 1000000
    mapping.max_runtime_seconds = 600

    config = {
        "pycolmap": p.__version__,
        "device": "cpu",
        "camera_mode": "SINGLE",
        "camera_model": "SIMPLE_PINHOLE",
        "prior_focal": focal,
        "reason": "DTU SampleSet rectified images share one camera; distortion already removed.",
        "extraction": extraction.todict(),
        "matching": matching.todict(),
        "mapping": mapping.todict(),
    }
    (output / "config.json").write_text(json.dumps(config, default=str, indent=2))

    database = output / "database.db"
    p.logging.minloglevel = 1
    started = time.perf_counter()
    try:
        status("extracting", images=len(prepared))
        camera = p.Camera.create_from_model_name(0, "SIMPLE_PINHOLE", focal, width, height)
        camera.has_prior_focal_length = True
        with p.Database.open(database) as db:
            camera_id = db.write_camera(camera)
        reader = p.ImageReaderOptions()
        reader.existing_camera_id = camera_id
        p.extract_features(
            database,
            images,
            reader_options=reader,
            extraction_options=extraction,
            device=p.Device.cpu,
        )
        extracted = time.perf_counter()
        status("matching")
        p.match_exhaustive(database, matching_options=matching, device=p.Device.cpu)
        matched = time.perf_counter()
        status("mapping")
        sparse = output / "sparse"
        sparse.mkdir()
        models = p.incremental_mapping(database, images, sparse, options=mapping)
        finished = time.perf_counter()
        results = []
        for index, model in models.items():
            directory = sparse / str(index)
            directory.mkdir(exist_ok=True)
            model.write(directory)
            model.export_PLY(str(directory / "points.ply"))
            registered = model.reg_image_ids()
            results.append(
                {
                    "id": index,
                    "registered_images": len(registered),
                    "points3D": model.num_points3D(),
                    "mean_reprojection_error_px": model.compute_mean_reprojection_error(),
                    "image_names": sorted(model.images[i].name for i in registered),
                }
            )
        summary = {
            "input_images": len(prepared),
            "models": results,
            "seconds": {
                "extraction": extracted - started,
                "matching": matched - extracted,
                "mapping": finished - matched,
                "total": finished - started,
            },
            "peak_process_ram_mib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024,
            "metric_scale_validated": False,
            "dtu_aligned": False,
            "note": "Sparse SfM in an arbitrary frame. Align to DTU STL before millimetre metrics.",
        }
        (output / "summary.json").write_text(json.dumps(summary, indent=2))
        status("complete", models=len(models))
        print(
            json.dumps(
                {
                    **summary,
                    "models": [{k: v for k, v in r.items() if k != "image_names"} for r in results],
                },
                indent=2,
            )
        )
    except BaseException as exc:
        status("failed", error=str(exc), elapsed_seconds=time.perf_counter() - started)
        raise


if __name__ == "__main__":
    main()
