"""Inspect / re-extract DTU SampleSet.zip with correct nested paths."""
from __future__ import annotations

import argparse
import zipfile
from collections import Counter
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("zip_path", type=Path)
    parser.add_argument("--extract-to", type=Path)
    parser.add_argument("--only-prefix", action="append", default=[])
    args = parser.parse_args()

    with zipfile.ZipFile(args.zip_path) as zf:
        names = zf.namelist()
        print(f"entries={len(names)}")
        print(f"uncompressed_gb={sum(i.file_size for i in zf.infolist()) / 1e9:.2f}")
        prefs = Counter("/".join(n.split("/")[:3]) for n in names if n.count("/") >= 2)
        for key, count in prefs.most_common(40):
            print(f"{count:6d} {key}")

        cleaned = sorted(
            {
                n.split("Cleaned/")[1].split("/")[0]
                for n in names
                if "/Cleaned/" in n and not n.endswith("/")
            }
        )
        rectified = sorted(
            {
                n.split("Rectified/")[1].split("/")[0]
                for n in names
                if "/Rectified/" in n and not n.endswith("/")
            }
        )
        print("cleaned_scans=", cleaned)
        print("rectified_scans=", rectified)

        if args.extract_to is None:
            return

        args.extract_to.mkdir(parents=True, exist_ok=True)
        selected = names
        if args.only_prefix:
            selected = [
                n
                for n in names
                if any(n.startswith(prefix) or f"/{prefix}" in n for prefix in args.only_prefix)
                or any(prefix in n for prefix in args.only_prefix)
            ]
            # Prefer exact path-component matches when prefixes look like scan folders.
            refined = []
            for n in names:
                keep = False
                for prefix in args.only_prefix:
                    if prefix in n:
                        keep = True
                        break
                if keep:
                    refined.append(n)
            selected = refined

        print(f"extracting={len(selected)} members to {args.extract_to}")
        for index, name in enumerate(selected, start=1):
            zf.extract(name, args.extract_to)
            if index % 200 == 0 or index == len(selected):
                print(f"extracted {index}/{len(selected)}", flush=True)


if __name__ == "__main__":
    main()
