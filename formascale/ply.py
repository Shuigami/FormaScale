"""Minimal PLY vertex reader for COLMAP fused.ply (xyz + optional rgb)."""
from __future__ import annotations

import struct
from pathlib import Path


_TYPE_SIZE = {
    "char": 1,
    "uchar": 1,
    "int8": 1,
    "uint8": 1,
    "short": 2,
    "ushort": 2,
    "int16": 2,
    "uint16": 2,
    "int": 4,
    "uint": 4,
    "int32": 4,
    "uint32": 4,
    "float": 4,
    "float32": 4,
    "double": 8,
    "float64": 8,
}

_STRUCT = {
    "char": "b",
    "uchar": "B",
    "int8": "b",
    "uint8": "B",
    "short": "h",
    "ushort": "H",
    "int16": "h",
    "uint16": "H",
    "int": "i",
    "uint": "I",
    "int32": "i",
    "uint32": "I",
    "float": "f",
    "float32": "f",
    "double": "d",
    "float64": "d",
}


def read_ply_points(path: Path, limit: int | None = None) -> tuple[list[dict], int]:
    """Return (points[{id,xyz,color}], total_vertex_count). Uniform subsample if limit set."""
    path = Path(path)
    with path.open("rb") as handle:
        if handle.readline().strip() != b"ply":
            raise ValueError(f"not a PLY file: {path}")
        fmt = None
        count = 0
        props: list[tuple[str, str]] = []
        in_vertex = False
        while True:
            raw = handle.readline()
            if not raw:
                raise ValueError(f"truncated PLY header: {path}")
            line = raw.decode("ascii", errors="replace").strip()
            if line.startswith("format "):
                fmt = line.split()[1]
            elif line.startswith("element vertex "):
                count = int(line.split()[2])
                in_vertex = True
                props = []
            elif line.startswith("element "):
                in_vertex = False
            elif line.startswith("property ") and in_vertex:
                parts = line.split()
                if parts[1] == "list":
                    raise ValueError("list properties unsupported in vertex PLY")
                props.append((parts[1], parts[2]))
            elif line == "end_header":
                break

        if fmt is None or count <= 0 or not props:
            raise ValueError(f"invalid PLY header: {path}")

        names = [name for _, name in props]
        if not {"x", "y", "z"}.issubset(names):
            raise ValueError(f"PLY missing xyz: {path}")

        if limit is None or limit >= count:
            indices = range(count)
        else:
            step = count / limit
            indices = [int(i * step) for i in range(limit)]

        if fmt == "ascii":
            points = _read_ascii(handle, props, count, indices)
        elif fmt.startswith("binary_"):
            little = "little" in fmt
            points = _read_binary(handle, props, count, indices, little)
        else:
            raise ValueError(f"unsupported PLY format {fmt}: {path}")
    return points, count


def _read_ascii(handle, props, count, indices) -> list[dict]:
    wanted = set(indices)
    points = []
    names = [name for _, name in props]
    for index in range(count):
        line = handle.readline()
        if index not in wanted:
            continue
        values = line.decode("ascii", errors="replace").split()
        row = {names[i]: values[i] for i in range(len(names))}
        points.append(_point(len(points), row))
    return points


def _read_binary(handle, props, count, indices, little: bool) -> list[dict]:
    endian = "<" if little else ">"
    fmt = endian + "".join(_STRUCT[ptype] for ptype, _ in props)
    stride = struct.calcsize(fmt)
    names = [name for _, name in props]
    wanted = set(indices)
    points = []
    for index in range(count):
        blob = handle.read(stride)
        if len(blob) != stride:
            raise ValueError("truncated PLY body")
        if index not in wanted:
            continue
        values = struct.unpack(fmt, blob)
        row = {names[i]: values[i] for i in range(len(names))}
        points.append(_point(len(points), row))
    return points


def _point(point_id: int, row: dict) -> dict:
    color = [
        int(row.get("red", 180)),
        int(row.get("green", 200)),
        int(row.get("blue", 210)),
    ]
    return {
        "id": point_id,
        "xyz": [float(row["x"]), float(row["y"]), float(row["z"])],
        "color": color,
        "error": 0.0,
        "track_length": 0,
    }
