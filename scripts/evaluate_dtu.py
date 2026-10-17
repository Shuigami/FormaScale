"""Evaluate a PLY against DTU STL ground truth, optionally aligning via camera centers.

Examples:
  # Smoke-test the official SampleSet Tola baseline (already in STL frame):
  python scripts/evaluate_dtu.py --scan 1 --pred datasets/dtu/SampleSet/MVS\\ Data/Points/tola/tola001_l3.ply

  # Align a FormaScale COLMAP model using DTU projection matrices, then evaluate:
  python scripts/evaluate_dtu.py --scan 1 --model captures/dtu_scan001/runs/sift_shared_01/sparse/0 --align-cameras
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import numpy as np
import pycolmap as p

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from formascale.dtu_eval import (  # noqa: E402
    camera_center_from_projection,
    evaluate_against_stl,
    load_dtu_projection,
    read_xyz_ply,
    transform_points,
    umeyama_similarity,
)


VIEW_RE = re.compile(r"rect_(\d{3})_")


def largest_model(sparse_dir: Path) -> Path:
    candidates = [path for path in sparse_dir.iterdir() if path.is_dir()]
    if not candidates:
        raise SystemExit(f"no models in {sparse_dir}")
    best = max(candidates, key=lambda path: p.Reconstruction(path).num_reg_images())
    return best


def align_model_to_dtu(model_dir: Path, calib_dir: Path, output_ply: Path) -> dict:
    model = p.Reconstruction(model_dir)
    source = []
    target = []
    used = []
    for image_id in model.reg_image_ids():
        image = model.images[image_id]
        match = VIEW_RE.search(image.name)
        if not match:
            continue
        view = int(match.group(1))
        proj_path = calib_dir / f"pos_{view:03d}.txt"
        if not proj_path.is_file():
            continue
        source.append(np.asarray(image.projection_center(), dtype=np.float64))
        target.append(camera_center_from_projection(load_dtu_projection(proj_path)))
        used.append({"image": image.name, "view": view})
    if len(source) < 3:
        raise SystemExit(f"need >=3 registered views with DTU poses, found {len(source)}")
    scale, rotation, translation = umeyama_similarity(np.asarray(source), np.asarray(target))
    residuals = np.linalg.norm(
        transform_points(np.asarray(source), scale, rotation, translation) - np.asarray(target),
        axis=1,
    )
    points = np.asarray([point.xyz for point in model.points3D.values()], dtype=np.float64)
    aligned = transform_points(points, scale, rotation, translation)
    output_ply.parent.mkdir(parents=True, exist_ok=True)
    with output_ply.open("w", encoding="utf-8") as handle:
        handle.write("ply\nformat ascii 1.0\n")
        handle.write(f"element vertex {len(aligned)}\n")
        handle.write("property float x\nproperty float y\nproperty float z\nend_header\n")
        for xyz in aligned:
            handle.write(f"{xyz[0]} {xyz[1]} {xyz[2]}\n")
    return {
        "aligned_cameras": len(used),
        "scale": scale,
        "mean_camera_center_error_mm": float(np.mean(residuals)),
        "median_camera_center_error_mm": float(np.median(residuals)),
        "max_camera_center_error_mm": float(np.max(residuals)),
        "output_ply": str(output_ply),
        "cameras_used": used,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dtu-root", type=Path, default=Path("datasets/dtu/SampleSet/MVS Data"))
    parser.add_argument("--scan", type=int, required=True)
    parser.add_argument("--pred", type=Path, help="Prediction PLY already in DTU/STL coordinates")
    parser.add_argument("--model", type=Path, help="COLMAP model directory or its parent sparse/")
    parser.add_argument("--align-cameras", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    stl_path = args.dtu_root / "Points" / "stl" / f"stl{args.scan:03d}_total.ply"
    obs_mask = args.dtu_root / "ObsMask" / f"ObsMask{args.scan}_10.mat"
    plane = args.dtu_root / "ObsMask" / f"Plane{args.scan}.mat"
    for path in (stl_path, obs_mask, plane):
        if not path.is_file():
            raise SystemExit(f"missing required DTU file: {path}")

    alignment = None
    if args.model is not None:
        model_dir = args.model
        if (model_dir / "sparse").is_dir():
            model_dir = largest_model(model_dir / "sparse")
        elif (model_dir / "0").is_dir() and not (model_dir / "cameras.bin").exists():
            model_dir = largest_model(model_dir)
        if not args.align_cameras:
            raise SystemExit("--model requires --align-cameras for DTU millimetre evaluation")
        out_dir = args.output or (model_dir.parents[1] / "dtu_eval")
        aligned_ply = out_dir / "points_aligned_mm.ply"
        alignment = align_model_to_dtu(
            model_dir,
            args.dtu_root / "Calibration" / "cal18",
            aligned_ply,
        )
        pred_points = read_xyz_ply(aligned_ply)
        report_dir = out_dir
    elif args.pred is not None:
        pred_points = read_xyz_ply(args.pred)
        report_dir = args.output or args.pred.with_suffix("").parent / f"dtu_eval_scan{args.scan:03d}"
    else:
        raise SystemExit("provide --pred or --model")

    report_dir.mkdir(parents=True, exist_ok=True)
    metrics = evaluate_against_stl(pred_points, read_xyz_ply(stl_path), obs_mask, plane)
    report = {
        "scan": args.scan,
        "stl": str(stl_path.resolve()),
        "prediction_points": int(len(pred_points)),
        "alignment": alignment,
        "metrics": metrics,
        "official_matlab_required_for_leaderboard": True,
    }
    (report_dir / "dtu_metrics.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
