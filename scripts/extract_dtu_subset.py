"""Extract the FormaScale DTU subset from SampleSet.zip, skipping existing files."""
from __future__ import annotations

import argparse
import zipfile
from pathlib import Path


NEEDED_PREFIXES = (
    "SampleSet/ReadMe.txt",
    "SampleSet/CVPR2014bib.txt",
    "SampleSet/Matlab evaluation code/",
    "SampleSet/MVS Data/Calibration/",
    "SampleSet/MVS Data/ObsMask/",
    "SampleSet/MVS Data/Points/",
    "SampleSet/MVS Data/Rectified/scan1/",
    "SampleSet/MVS Data/Rectified/scan6/",
)


def wanted(name: str) -> bool:
    if name.endswith("/"):
        return False
    return any(name.startswith(prefix) for prefix in NEEDED_PREFIXES)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("zip_path", type=Path)
    parser.add_argument("dest", type=Path)
    args = parser.parse_args()
    args.dest.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(args.zip_path) as zf:
        members = [info for info in zf.infolist() if wanted(info.filename)]
        print(f"selected={len(members)}", flush=True)
        extracted = skipped = 0
        for index, info in enumerate(members, start=1):
            target = args.dest / info.filename
            if target.is_file() and target.stat().st_size == info.file_size:
                skipped += 1
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as src, target.open("wb") as dst:
                    while True:
                        chunk = src.read(1024 * 1024)
                        if not chunk:
                            break
                        dst.write(chunk)
                extracted += 1
            if index % 100 == 0 or index == len(members):
                print(
                    f"progress {index}/{len(members)} extracted={extracted} skipped={skipped}",
                    flush=True,
                )
    print("done", flush=True)


if __name__ == "__main__":
    main()
