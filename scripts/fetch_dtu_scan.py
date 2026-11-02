"""Fetch one lighting of one DTU SampleSet rectified scan without the full 6 GB zip.

Example:
  python scripts/fetch_dtu_scan.py --scan 1 --light 3
Writes datasets/dtu/SampleSet/MVS Data/Rectified/scan1/rect_XXX_3_r5000.png,
the layout expected by scripts/prepare_dtu_capture.py.

The archive is read with HTTP range requests (remotezip), so only the selected
PNG files are transferred. If the server refuses ranges, pass --full to download
the whole archive once and extract the same subset from it.
Citeware: Jensen et al., Large Scale Multi-view Stereopsis Evaluation, CVPR 2014.
"""
from __future__ import annotations

import argparse
import re
import shutil
import sys
import urllib.request
import zipfile
from pathlib import Path

DEFAULT_URL = "http://roboimagedata2.compute.dtu.dk/data/MVS/SampleSet.zip"


def selected(names: list[str], scan: int, light: int) -> list[str]:
    pattern = re.compile(
        rf"^SampleSet/MVS Data/Rectified/scan{scan}/rect_\d{{3}}_{light}_r5000\.png$"
    )
    return sorted(name for name in names if pattern.match(name))


def extract(zf: zipfile.ZipFile, members: list[str], dest: Path) -> int:
    written = 0
    for index, name in enumerate(members, start=1):
        info = zf.getinfo(name)
        target = dest / name
        if target.is_file() and target.stat().st_size == info.file_size:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(info) as src, target.open("wb") as dst:
            shutil.copyfileobj(src, dst, 1024 * 1024)
        written += 1
        print(f"[{index}/{len(members)}] {name}", flush=True)
    return written


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--scan", type=int, default=1)
    parser.add_argument("--light", type=int, default=3)
    parser.add_argument("--dest", type=Path, default=Path("datasets/dtu"))
    parser.add_argument("--full", action="store_true", help="download the whole zip instead of ranges")
    parser.add_argument("--zip-cache", type=Path, default=Path("datasets/SampleSet.zip"))
    args = parser.parse_args()

    if not args.full:
        try:
            from remotezip import RemoteZip

            with RemoteZip(args.url) as zf:
                members = selected(zf.namelist(), args.scan, args.light)
                if not members:
                    raise SystemExit(f"no rectified images for scan{args.scan} light {args.light}")
                written = extract(zf, members, args.dest)
                print(f"selected={len(members)} written={written}")
                return
        except SystemExit:
            raise
        except Exception as exc:  # range requests unsupported, network error, ...
            print(f"range download failed ({exc!r}); falling back to full download", file=sys.stderr)

    if not args.zip_cache.is_file():
        args.zip_cache.parent.mkdir(parents=True, exist_ok=True)
        partial = args.zip_cache.with_suffix(".part")
        print(f"downloading {args.url} -> {args.zip_cache}", flush=True)
        with urllib.request.urlopen(args.url) as response, partial.open("wb") as out:
            shutil.copyfileobj(response, out, 8 * 1024 * 1024)
        partial.rename(args.zip_cache)
    with zipfile.ZipFile(args.zip_cache) as zf:
        members = selected(zf.namelist(), args.scan, args.light)
        if not members:
            raise SystemExit(f"no rectified images for scan{args.scan} light {args.light}")
        written = extract(zf, members, args.dest)
        print(f"selected={len(members)} written={written}")


if __name__ == "__main__":
    main()
