"""Assemble a static GitHub Pages site from the viewer and selected captures.

Example:
  python scripts/build_pages_site.py dtu_scan001 --out _site
Layout keeps the viewer's relative paths unchanged:
  _site/index.html                                -> redirects to viewer/
  _site/viewer/                                   -> viewer files + catalog of published captures
  _site/captures/<name>/reports/viewer-data.js
  _site/captures/<name>/prepared/images|sfm_subset/
Only captures named on the command line are published.
"""
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

INDEX = """<!doctype html>
<html lang="fr">
<head>
  <meta charset="utf-8">
  <meta http-equiv="refresh" content="0; url=viewer/">
  <title>FormaScale</title>
</head>
<body><p><a href="viewer/">Ouvrir le viewer FormaScale</a></p></body>
</html>
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("captures", nargs="+")
    parser.add_argument("--captures-root", type=Path, default=ROOT / "captures")
    parser.add_argument("--out", type=Path, default=ROOT / "_site")
    args = parser.parse_args()

    out = args.out.resolve()
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(ROOT / "viewer", out / "viewer")
    # viewer/style.css loads its font from ../live/static/fonts/.
    shutil.copytree(ROOT / "live" / "static" / "fonts", out / "live" / "static" / "fonts")

    entries = []
    for name in args.captures:
        if Path(name).name != name:
            parser.error(f"capture must be a single directory name: {name}")
        source = args.captures_root / name
        data_path = source / "reports" / "viewer-data.js"
        if not data_path.is_file():
            raise SystemExit(f"missing {data_path}; run export_diagnostics.py first")
        target = out / "captures" / name
        (target / "reports").mkdir(parents=True)
        shutil.copy2(data_path, target / "reports" / "viewer-data.js")
        for folder in ("images", "sfm_subset"):
            images = source / "prepared" / folder
            if images.is_dir():
                shutil.copytree(images, target / "prepared" / folder)
        entries.append(
            {
                "id": name,
                "label": name.replace("_", " "),
                "kind": "dtu" if name.startswith("dtu_") else "capture",
                "data_url": f"../captures/{name}/reports/viewer-data.js",
                "image_base": f"../captures/{name}/prepared/images/",
            }
        )

    (out / "viewer" / "catalog.js").write_text(
        "window.FORMA_CATALOG = " + json.dumps(entries, indent=2) + ";\n", encoding="utf-8"
    )
    (out / "index.html").write_text(INDEX, encoding="utf-8")
    (out / ".nojekyll").write_text("", encoding="utf-8")

    size = sum(path.stat().st_size for path in out.rglob("*") if path.is_file())
    print(json.dumps({"site": str(out), "captures": args.captures, "mb": round(size / 1e6, 1)}, indent=2))
    if size > 900e6:
        raise SystemExit("site exceeds 900 MB; GitHub Pages caps published sites at 1 GB")


if __name__ == "__main__":
    main()
