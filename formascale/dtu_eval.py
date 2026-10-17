"""DTU MVS accuracy / completeness evaluation (Python port of the official protocol).

Distances are in millimetres. Official MATLAB code remains the reference for
leaderboard numbers; this port is for local, reproducible FormaScale runs.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
from plyfile import PlyData
from scipy.io import loadmat
from scipy.spatial import cKDTree


def read_xyz_ply(path: Path) -> np.ndarray:
    ply = PlyData.read(str(path))
    vertex = ply["vertex"]
    return np.column_stack([vertex["x"], vertex["y"], vertex["z"]]).astype(np.float64)


def reduce_points(points: np.ndarray, spacing_mm: float = 0.2) -> np.ndarray:
    """Keep at most one point per spacing_mm voxel (DTU default 0.2 mm)."""
    if len(points) == 0:
        return points
    if spacing_mm <= 0:
        raise ValueError("spacing_mm must be positive")
    keys = np.floor(points / spacing_mm).astype(np.int64)
    _, unique = np.unique(keys, axis=0, return_index=True)
    return points[np.sort(unique)]


def camera_center_from_projection(matrix_3x4: np.ndarray) -> np.ndarray:
    """Camera center as the null-space of the 3x4 projection matrix."""
    _, _, vh = np.linalg.svd(np.asarray(matrix_3x4, dtype=np.float64))
    center = vh[-1]
    if abs(center[3]) < 1e-12:
        raise ValueError("degenerate projection matrix")
    return center[:3] / center[3]


def load_dtu_projection(path: Path) -> np.ndarray:
    values = np.loadtxt(path, dtype=np.float64)
    if values.size != 12:
        raise ValueError(f"expected 3x4 projection in {path}")
    return values.reshape(3, 4)


def umeyama_similarity(source: np.ndarray, target: np.ndarray) -> tuple[float, np.ndarray, np.ndarray]:
    """Estimate target ≈ scale * R @ source + t. Returns scale, R, t."""
    source = np.asarray(source, dtype=np.float64)
    target = np.asarray(target, dtype=np.float64)
    if source.shape != target.shape or source.ndim != 2 or source.shape[1] != 3 or len(source) < 3:
        raise ValueError("need at least three corresponding 3D points")
    mu_s = source.mean(axis=0)
    mu_t = target.mean(axis=0)
    source_c = source - mu_s
    target_c = target - mu_t
    var_s = np.mean(np.sum(source_c**2, axis=1))
    if var_s < 1e-16:
        raise ValueError("source points are degenerate")
    cov = (target_c.T @ source_c) / len(source)
    u, d, vh = np.linalg.svd(cov)
    s = np.eye(3)
    if np.linalg.det(u) * np.linalg.det(vh) < 0:
        s[2, 2] = -1
    rotation = u @ s @ vh
    scale = np.trace(np.diag(d) @ s) / var_s
    translation = mu_t - scale * rotation @ mu_s
    return float(scale), rotation, translation


def transform_points(points: np.ndarray, scale: float, rotation: np.ndarray, translation: np.ndarray) -> np.ndarray:
    return (scale * (np.asarray(points, dtype=np.float64) @ rotation.T)) + translation


def evaluate_against_stl(
    data_points: np.ndarray,
    stl_points: np.ndarray,
    obs_mask_mat: Path,
    plane_mat: Path,
    *,
    spacing_mm: float = 0.2,
    max_dist_mm: float = 20.0,
    search_max_mm: float = 60.0,
) -> dict[str, Any]:
    """Return DTU-style mean/median/var accuracy (data→stl) and completeness (stl→data)."""
    mask_data = loadmat(str(obs_mask_mat))
    plane_data = loadmat(str(plane_mat))
    obs_mask = np.asarray(mask_data["ObsMask"], dtype=bool)
    bounding_box = np.asarray(mask_data["BB"], dtype=np.float64)
    resolution = float(np.asarray(mask_data["Res"]).reshape(-1)[0])
    plane = np.asarray(plane_data["P"], dtype=np.float64).reshape(4)

    data = reduce_points(np.asarray(data_points, dtype=np.float64), spacing_mm)
    stl = reduce_points(np.asarray(stl_points, dtype=np.float64), spacing_mm)
    if len(data) == 0 or len(stl) == 0:
        raise ValueError("empty point cloud after reduction")

    stl_tree = cKDTree(stl)
    data_tree = cKDTree(data)
    data_to_stl, _ = stl_tree.query(data, distance_upper_bound=search_max_mm)
    stl_to_data, _ = data_tree.query(stl, distance_upper_bound=search_max_mm)
    data_to_stl = np.where(np.isfinite(data_to_stl), data_to_stl, search_max_mm)
    stl_to_data = np.where(np.isfinite(stl_to_data), stl_to_data, search_max_mm)

    voxel = np.round((data - bounding_box[0]) / resolution).astype(np.int64)
    in_bounds = (
        (voxel[:, 0] >= 0)
        & (voxel[:, 0] < obs_mask.shape[0])
        & (voxel[:, 1] >= 0)
        & (voxel[:, 1] < obs_mask.shape[1])
        & (voxel[:, 2] >= 0)
        & (voxel[:, 2] < obs_mask.shape[2])
    )
    data_in_mask = np.zeros(len(data), dtype=bool)
    valid = np.flatnonzero(in_bounds)
    if len(valid):
        idx = voxel[valid]
        data_in_mask[valid] = obs_mask[idx[:, 0], idx[:, 1], idx[:, 2]]

    stl_above_plane = (plane[:3] @ stl.T + plane[3]) > 0

    accuracy_distances = data_to_stl[data_in_mask]
    accuracy_distances = accuracy_distances[accuracy_distances < max_dist_mm]
    completeness_distances = stl_to_data[stl_above_plane]
    completeness_distances = completeness_distances[completeness_distances < max_dist_mm]

    def summarize(values: np.ndarray) -> dict[str, float | int]:
        if len(values) == 0:
            return {"count": 0, "mean_mm": float("nan"), "median_mm": float("nan"), "var_mm2": float("nan")}
        return {
            "count": int(len(values)),
            "mean_mm": float(np.mean(values)),
            "median_mm": float(np.median(values)),
            "var_mm2": float(np.var(values)),
        }

    return {
        "spacing_mm": spacing_mm,
        "outlier_threshold_mm": max_dist_mm,
        "data_points_reduced": int(len(data)),
        "stl_points_reduced": int(len(stl)),
        "accuracy_data_to_stl": summarize(accuracy_distances),
        "completeness_stl_to_data": summarize(completeness_distances),
        "protocol_note": (
            "Python port of PointCompareMain/ComputeStat_web. "
            "Use official MATLAB scripts for publication leaderboard claims."
        ),
    }
