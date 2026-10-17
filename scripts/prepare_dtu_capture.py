"""Prepare a FormaScale capture folder from DTU SampleSet Rectified images.

Example:
  python scripts/prepare_dtu_capture.py --scan 1 --run-name dtu_scan001
Uses lighting index 3 (all lights on), which matches the SampleSet l3 baselines.
"""
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from PIL import Image


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dtu-root", type=Path, default=Path("datasets/dtu/SampleSet/MVS Data"))
    parser.add_argument("--scan", type=int, required=True, choices=[1, 6])
    parser.add_argument("--light", type=int, default=3)
    parser.add_argument("--run-name", type=Path, required=True)
    parser.add_argument("--captures-root", type=Path, default=Path("captures"))
    parser.add_argument("--max-dim", type=int, default=2000)
    args = parser.parse_args()

    if Path(args.run_name).name != str(args.run_name):
        parser.error("--run-name must be a single directory name")

    scan_dir = args.dtu_root / "Rectified" / f"scan{args.scan}"
    if not scan_dir.is_dir():
        raise SystemExit(f"missing rectified scan folder: {scan_dir}")

    capture = args.captures_root / args.run_name
    if capture.exists():
        raise SystemExit(f"capture already exists: {capture}")
    images = capture / "images"
    prepared = capture / "prepared" / "images"
    images.mkdir(parents=True)
    prepared.mkdir(parents=True)

    sources = sorted(scan_dir.glob(f"rect_*_{args.light}_r5000.png"))
    if len(sources) < 3:
        raise SystemExit(f"expected at least 3 images, found {len(sources)} in {scan_dir}")

    records = []
    for source in sources:
        destination_png = images / source.name
        shutil.copy2(source, destination_png)
        with Image.open(source) as im:
            rgb = im.convert("RGB")
            rgb.thumbnail((args.max_dim, args.max_dim), Image.Resampling.LANCZOS)
            prepared_name = source.stem + ".jpg"
            rgb.save(prepared / prepared_name, quality=95, subsampling=0)
            records.append(
                {
                    "source": source.name,
                    "prepared": prepared_name,
                    "prepared_size": list(rgb.size),
                    "light": args.light,
                }
            )

    notes = {
        "dataset": "DTU MVS SampleSet",
        "scan": args.scan,
        "light": args.light,
        "light_meaning": "3 = all lights on (official SampleSet l3 setting)",
        "images": len(records),
        "coordinate_note": (
            "Projection matrices in Calibration/cal18/pos_XXX.txt are in the "
            "structured-light / STL frame. SfM reconstructions need Sim(3) alignment "
            "before DTU accuracy/completeness evaluation."
        ),
        "stl": f"Points/stl/stl{args.scan:03d}_total.ply",
        "obs_mask": f"ObsMask/ObsMask{args.scan}_10.mat",
        "plane": f"ObsMask/Plane{args.scan}.mat",
        "cite": (
            "Jensen et al., Large Scale Multi-view Stereopsis Evaluation, CVPR 2014"
        ),
    }
    (capture / "notes.json").write_text(json.dumps({**notes, "files": records}, indent=2), encoding="utf-8")
    (capture / "notes.txt").write_text(
        "\n".join(
            [
                f"dataset: DTU SampleSet scan{args.scan}",
                f"light: {args.light}",
                f"images: {len(records)}",
                "scale: millimetres after alignment to STL",
                "metric_validation: DTU accuracy/completeness vs STL",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"capture": str(capture), "images": len(records)}, indent=2))


if __name__ == "__main__":
    main()
