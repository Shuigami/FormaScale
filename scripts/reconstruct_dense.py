"""Dense MVS (PatchMatch GPU) + Poisson mesh from an existing sparse COLMAP run.

Requires a CUDA-enabled `colmap` binary (FormaScale default:
~/mamba/envs/colmap-cuda via micromamba). pycolmap CPU is enough for sparse.

Example:
  python scripts/reconstruct_dense.py captures/live_20261017_170736 --run live
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import time
from pathlib import Path


def resolve_colmap(explicit: str | None) -> tuple[list[str], Path]:
    """Return (argv_prefix, binary_path) with libs resolved for the conda env."""
    if explicit:
        path = Path(explicit).expanduser().resolve()
        if not path.is_file():
            raise SystemExit(f"colmap not found: {path}")
        return [str(path)], path

    env_bin = Path.home() / "mamba" / "envs" / "colmap-cuda" / "bin" / "colmap"
    if env_bin.is_file():
        return [str(env_bin)], env_bin

    which = shutil.which("colmap")
    if which:
        path = Path(which)
        return [str(path)], path

    raise SystemExit(
        "CUDA COLMAP introuvable. Installe l'env micromamba `colmap-cuda` "
        "(voir README) ou passe --colmap /chemin/vers/colmap"
    )


def colmap_env(binary: Path) -> dict[str, str]:
    env = os.environ.copy()
    # Keep Windows junk out of PATH so subprocess does not choke on spaces/'()'.
    clean = ["/usr/bin", "/bin", str(binary.parent)]
    lib = binary.parent.parent / "lib"
    if lib.is_dir():
        clean.insert(0, str(lib))
        prev = env.get("LD_LIBRARY_PATH", "")
        env["LD_LIBRARY_PATH"] = str(lib) + ((":" + prev) if prev else "")
    wsl_lib = Path("/usr/lib/wsl/lib")
    if wsl_lib.is_dir():
        env["LD_LIBRARY_PATH"] = (
            str(wsl_lib) + ":" + env.get("LD_LIBRARY_PATH", "")
        ).rstrip(":")
    env["PATH"] = ":".join(clean)
    env["MAMBA_ROOT_PREFIX"] = str(Path.home() / "mamba")
    return env


def pick_sparse_model(sparse_root: Path, preferred_id: int | None) -> Path:
    if preferred_id is not None:
        candidate = sparse_root / str(preferred_id)
        if (candidate / "images.bin").is_file() or (candidate / "images.txt").is_file():
            return candidate
    models = []
    for child in sparse_root.iterdir():
        if not child.is_dir():
            continue
        if (child / "images.bin").is_file() or (child / "images.txt").is_file():
            models.append(child)
    if not models:
        raise SystemExit(f"aucun modèle sparse sous {sparse_root}")
    # Prefer numeric folder names; fall back to largest points3D if present.
    def score(path: Path) -> tuple[int, int]:
        points = path / "points3D.bin"
        size = points.stat().st_size if points.is_file() else 0
        try:
            index = int(path.name)
        except ValueError:
            index = -1
        return (size, index)

    return max(models, key=score)


def resolve_images(capture: Path, run_dir: Path) -> Path:
    subset = capture / "prepared" / "sfm_subset"
    if subset.is_dir() and any(subset.glob("*.jpg")):
        return subset
    images = capture / "prepared" / "images"
    if images.is_dir() and any(images.glob("*.jpg")):
        return images
    raise SystemExit(f"pas d'images préparées dans {capture}")


def run_colmap(prefix: list[str], binary: Path, args: list[str]) -> None:
    cmd = [*prefix, *args]
    print("+", " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True, env=colmap_env(binary))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture", type=Path)
    parser.add_argument("--run", required=True)
    parser.add_argument("--colmap", default=None, help="Path to CUDA colmap binary")
    parser.add_argument("--max-image-size", type=int, default=1600)
    parser.add_argument("--gpu-index", type=int, default=0)
    parser.add_argument(
        "--skip-mesh",
        action="store_true",
        help="Stop after fused.ply (skip Poisson)",
    )
    args = parser.parse_args()
    if Path(args.run).name != args.run or args.run in {".", ".."}:
        parser.error("--run must be a single directory name")

    capture = args.capture.resolve()
    run_dir = capture / "runs" / args.run
    sparse_root = run_dir / "sparse"
    if not sparse_root.is_dir():
        raise SystemExit(f"sparse manquant: {sparse_root}")

    preferred = None
    summary_path = run_dir / "summary.json"
    if summary_path.is_file():
        try:
            preferred = json.loads(summary_path.read_text(encoding="utf-8")).get("model_id")
        except json.JSONDecodeError:
            preferred = None

    sparse_model = pick_sparse_model(sparse_root, preferred)
    images = resolve_images(capture, run_dir)
    dense = run_dir / "dense"
    dense.mkdir(parents=True, exist_ok=True)
    status_path = dense / "status.json"

    def status(stage: str, **extra) -> None:
        payload = {"stage": stage, **extra}
        status_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(stage, flush=True)

    prefix, binary = resolve_colmap(args.colmap)
    help_out = subprocess.run(
        [*prefix, "help"],
        capture_output=True,
        text=True,
        env=colmap_env(binary),
        check=False,
    )
    banner = (help_out.stdout or "") + (help_out.stderr or "")
    if "CUDA" not in banner:
        raise SystemExit(
            f"{binary} ne signale pas CUDA. Installe colmap-cuda (README)."
        )

    started = time.perf_counter()
    timings: dict[str, float] = {}

    status("undistort", images=str(images), sparse=str(sparse_model))
    t0 = time.perf_counter()
    run_colmap(
        prefix,
        binary,
        [
            "image_undistorter",
            "--image_path", str(images),
            "--input_path", str(sparse_model),
            "--output_path", str(dense),
            "--output_type", "COLMAP",
            "--max_image_size", str(args.max_image_size),
        ],
    )
    timings["undistort"] = round(time.perf_counter() - t0, 2)

    status("patch_match_stereo", gpu_index=args.gpu_index)
    t0 = time.perf_counter()
    run_colmap(
        prefix,
        binary,
        [
            "patch_match_stereo",
            "--workspace_path", str(dense),
            "--workspace_format", "COLMAP",
            "--PatchMatchStereo.gpu_index", str(args.gpu_index),
            "--PatchMatchStereo.max_image_size", str(args.max_image_size),
            "--PatchMatchStereo.cache_size", "8",
        ],
    )
    timings["patch_match_stereo"] = round(time.perf_counter() - t0, 2)

    fused = dense / "fused.ply"
    status("stereo_fusion")
    t0 = time.perf_counter()
    run_colmap(
        prefix,
        binary,
        [
            "stereo_fusion",
            "--workspace_path", str(dense),
            "--workspace_format", "COLMAP",
            "--input_type", "geometric",
            "--output_path", str(fused),
            "--StereoFusion.max_image_size", str(args.max_image_size),
            "--StereoFusion.cache_size", "8",
        ],
    )
    timings["stereo_fusion"] = round(time.perf_counter() - t0, 2)
    if not fused.is_file() or fused.stat().st_size < 100:
        raise SystemExit(f"fused.ply manquant ou vide: {fused}")

    mesh = dense / "meshed-poisson.ply"
    if not args.skip_mesh:
        status("poisson_meshing")
        t0 = time.perf_counter()
        run_colmap(
            prefix,
            binary,
            [
                "poisson_mesher",
                "--input_path", str(fused),
                "--output_path", str(mesh),
                "--PoissonMeshing.depth", "11",
                "--PoissonMeshing.trim", "7",
            ],
        )
        timings["poisson_meshing"] = round(time.perf_counter() - t0, 2)
        if not mesh.is_file():
            raise SystemExit(f"mesh manquant: {mesh}")

    elapsed = round(time.perf_counter() - started, 2)
    summary = {
        "capture": str(capture),
        "run": args.run,
        "sparse_model": str(sparse_model.relative_to(capture)),
        "images": str(images.relative_to(capture)),
        "colmap": str(binary),
        "device": f"cuda:{args.gpu_index}",
        "max_image_size": args.max_image_size,
        "fused_ply": str(fused.relative_to(capture)),
        "fused_bytes": fused.stat().st_size,
        "mesh_ply": str(mesh.relative_to(capture)) if mesh.is_file() else None,
        "mesh_bytes": mesh.stat().st_size if mesh.is_file() else None,
        "seconds": {"stages": timings, "total": elapsed},
        "note": (
            "Dense MVS via COLMAP PatchMatch (GPU). "
            "Metric scale is not validated by densification alone."
        ),
    }
    (dense / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    status("done", seconds=elapsed, fused_bytes=summary["fused_bytes"])
    print(json.dumps(summary, indent=2), flush=True)


if __name__ == "__main__":
    main()
