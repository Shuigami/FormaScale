"""Build a self-contained diagnostic dataset from saved COLMAP runs.

Example:
  python scripts/export_diagnostics.py captures/dtu_scan001
  python scripts/export_diagnostics.py captures/bose_01 --refresh-catalog
  python scripts/export_diagnostics.py captures/live_20261017_170736 --refresh-catalog
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import json
import sys
from pathlib import Path
import sqlite3

import pycolmap as p

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from formascale.ply import read_ply_points


def downsample_points(points: list[dict], limit: int) -> list[dict]:
    if len(points) <= limit:
        return points
    ranked = sorted(points, key=lambda point: (-point["track_length"], point["error"], point["id"]))
    return ranked[:limit]


def normalize_summary(summary: dict) -> dict:
    out = dict(summary)
    if "models" not in out:
        mid = out.get("model_id", 0)
        out["models"] = [
            {
                "id": mid,
                "registered_images": out.get("registered", 0),
                "points3D": out.get("points3D", 0),
                "mean_reprojection_error_px": out.get("mean_reprojection_error_px", 0.0),
            }
        ]
    if "input_images" not in out:
        out["input_images"] = out.get("used_for_sfm") or out.get("keyframes") or 0
    seconds = out.get("seconds")
    if not isinstance(seconds, dict):
        out["seconds"] = {"total": float(seconds or 0)}
    return out


def export_dense_model(run: Path, sparse_images: list[dict], point_limit: int) -> dict | None:
    fused = run / "dense" / "fused.ply"
    if not fused.is_file():
        return None
    points, total = read_ply_points(fused, limit=point_limit)
    cameras = []
    for image in sparse_images:
        cameras.append(
            {
                "id": image["id"],
                "name": image["name"],
                "camera_id": image.get("camera_id", 0),
                "center": image["center"],
                "width": image["width"],
                "height": image["height"],
                "observations": [],
                "camera_params": image.get("camera_params", []),
            }
        )
    return {
        "id": "dense",
        "kind": "dense",
        "points": points,
        "images": cameras,
        "reprojection_error": 0.0,
        "points_displayed": len(points),
        "points_total": total,
    }


def export_run(run: Path, point_limit: int) -> dict:
    summary = normalize_summary(json.loads((run / "summary.json").read_text(encoding="utf-8")))
    names: dict = {}
    keypoints: dict = {}
    pairs = []
    degrees: dict = defaultdict(int)
    db_path = run / "database.db"
    if db_path.is_file():
        with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as db:
            names = dict(db.execute("select image_id,name from images"))
            keypoints = dict(db.execute("select image_id,rows from keypoints"))
            for pair_id, count in db.execute(
                "select pair_id,rows from two_view_geometries where rows>=15"
            ):
                left, right = p.pair_id_to_image_pair(pair_id)
                pairs.append({"a": names[left], "b": names[right], "inliers": count})
                degrees[left] += 1
                degrees[right] += 1

    models = []
    for saved in summary["models"]:
        directory = run / "sparse" / str(saved["id"])
        if not directory.is_dir():
            continue
        model = p.Reconstruction(directory)
        points = [
            {
                "id": int(key),
                "xyz": value.xyz.tolist(),
                "color": value.color.tolist(),
                "error": float(value.error),
                "track_length": value.track.length(),
            }
            for key, value in model.points3D.items()
        ]
        points = downsample_points(points, point_limit)
        kept = {point["id"] for point in points}
        images = []
        for image_id in model.reg_image_ids():
            image = model.images[image_id]
            camera = model.cameras[image.camera_id]
            observations = [
                {"xy": obs.xy.tolist(), "point": int(obs.point3D_id)}
                for obs in image.points2D
                if obs.has_point3D() and int(obs.point3D_id) in kept
            ]
            images.append(
                {
                    "id": image_id,
                    "name": image.name,
                    "camera_id": image.camera_id,
                    "center": image.projection_center().tolist(),
                    "width": camera.width,
                    "height": camera.height,
                    "observations": observations,
                    "camera_params": camera.params.tolist(),
                }
            )
        models.append(
            {
                "id": saved["id"],
                "kind": "sparse",
                "points": points,
                "images": sorted(images, key=lambda item: item["name"]),
                "reprojection_error": saved["mean_reprojection_error_px"],
                "points_displayed": len(points),
                "points_total": saved["points3D"],
            }
        )

    if models:
        dense = export_dense_model(run, models[0]["images"], point_limit)
        if dense is not None:
            models.append(dense)

    dtu_metrics = None
    metrics_path = run / "dtu_eval" / "dtu_metrics.json"
    if metrics_path.is_file():
        dtu_metrics = json.loads(metrics_path.read_text(encoding="utf-8"))

    image_rows = [
        {
            "id": image_id,
            "name": name,
            "features": keypoints.get(image_id, 0),
            "connected_views": degrees[image_id],
        }
        for image_id, name in names.items()
    ]
    if not image_rows and models:
        image_rows = [
            {"id": im["id"], "name": im["name"], "features": 0, "connected_views": 0}
            for im in models[0]["images"]
        ]

    return {
        "name": run.name,
        "summary": summary,
        "models": models,
        "pairs": pairs,
        "dtu_metrics": dtu_metrics,
        "images": image_rows,
    }


def write_catalog(project_root: Path) -> Path:
    captures_root = project_root / "captures"
    entries = []
    for capture_dir in sorted(captures_root.iterdir()):
        data_path = capture_dir / "reports" / "viewer-data.js"
        if not data_path.is_file():
            continue
        label = capture_dir.name.replace("_", " ")
        kind = "dtu" if capture_dir.name.startswith("dtu_") else "capture"
        entries.append(
            {
                "id": capture_dir.name,
                "label": label,
                "kind": kind,
                "data_url": f"../captures/{capture_dir.name}/reports/viewer-data.js",
                "image_base": f"../captures/{capture_dir.name}/prepared/images/",
            }
        )
    output = project_root / "viewer" / "catalog.js"
    output.write_text(
        "window.FORMA_CATALOG = " + json.dumps(entries, indent=2) + ";\n",
        encoding="utf-8",
    )
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture", type=Path, nargs="?")
    parser.add_argument("--point-limit", type=int, default=20000)
    parser.add_argument("--refresh-catalog", action="store_true")
    args = parser.parse_args()
    project_root = Path(__file__).resolve().parents[1]

    if args.capture is not None:
        root = args.capture.resolve()
        runs = []
        for directory in sorted((root / "runs").iterdir()):
            if (directory / "summary.json").is_file() and (
                (directory / "database.db").is_file() or (directory / "sparse").is_dir()
            ):
                runs.append(export_run(directory, args.point_limit))
        notes_path = root / "notes.json"
        notes = json.loads(notes_path.read_text(encoding="utf-8")) if notes_path.is_file() else {}
        image_base = f"../captures/{root.name}/prepared/images/"
        subset = root / "prepared" / "sfm_subset"
        if runs and runs[0]["models"] and runs[0]["models"][0]["images"]:
            sample = runs[0]["models"][0]["images"][0]["name"]
            if subset.is_dir() and (subset / sample).is_file():
                image_base = f"../captures/{root.name}/prepared/sfm_subset/"
        data = {
            "capture": root.name,
            "kind": "dtu" if root.name.startswith("dtu_") else "capture",
            "reference_mm": notes.get("scale_reference_mm", 100),
            "metric_scale_validated": False,
            "notes": notes,
            "image_base": image_base,
            "runs": runs,
        }
        output = root / "reports" / "viewer-data.js"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            "window.FORMA_DATA = " + json.dumps(data, allow_nan=False) + ";\n",
            encoding="utf-8",
        )
        print(
            json.dumps(
                {
                    "capture": root.name,
                    "bytes": output.stat().st_size,
                    "runs": [
                        {
                            "run": run["name"],
                            "models": [
                                {
                                    "id": model["id"],
                                    "kind": model.get("kind", "sparse"),
                                    "images": len(model["images"]),
                                    "points": len(model["points"]),
                                    "points_total": model.get("points_total"),
                                }
                                for model in run["models"]
                            ],
                        }
                        for run in runs
                    ],
                },
                indent=2,
            )
        )

    if args.refresh_catalog or args.capture is not None:
        catalog = write_catalog(project_root)
        print(
            json.dumps(
                {
                    "catalog": str(catalog),
                    "entries": json.loads(
                        catalog.read_text().split("=", 1)[1].rstrip(";\n")
                    ),
                },
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
