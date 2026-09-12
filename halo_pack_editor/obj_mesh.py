"""Halo 2.0.0 OBJ subset and Blender geometry bridge.

The parser mirrors HaloCore's ``ObjMeshLoader``: textured triangles and
planar convex quads, independent positive/negative indices, ignored names and
MTL declarations, and fixed resource guards.  Parsed UVs use the mod's
top-left convention; Blender conversion flips V back exactly once.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from math import isfinite, sqrt
from pathlib import Path
import re
import shutil
import struct
from typing import Iterable, Sequence

from .core.resource_paths import lowercase_resource_identifier


MAX_TEXT_LENGTH = 16 * 1024 * 1024
MAX_ELEMENTS = 1_000_000
MAX_TRIANGLES = 250_000


@dataclass(frozen=True)
class ObjMeshData:
    positions: tuple[tuple[float, float, float], ...]
    uvs: tuple[tuple[float, float], ...]
    triangles: tuple[tuple[int, int, int], ...]
    minimum: tuple[float, float, float]
    maximum: tuple[float, float, float]

    @property
    def vertex_count(self) -> int:
        return len(self.positions)

    @property
    def triangle_count(self) -> int:
        return len(self.triangles)


def _sub(a, b):
    return tuple(a[index] - b[index] for index in range(3))


def _cross(a, b):
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _dot(a, b):
    return sum(a[index] * b[index] for index in range(3))


def _float32(value: float) -> float:
    try:
        return struct.unpack("!f", struct.pack("!f", float(value)))[0]
    except OverflowError:
        return float("inf") if value >= 0 else float("-inf")


def _finite(word: str) -> float:
    value = _float32(float(word))
    if not isfinite(value):
        raise ValueError(f"Non-finite number: {word}")
    return value


def _index(word: str, size: int) -> int:
    raw = int(word)
    index = size + raw if raw < 0 else raw - 1
    if raw == 0 or index < 0 or index >= size:
        raise ValueError(f"OBJ index out of bounds: {word}")
    return index


def parse_obj(text: str, resource_id: str = "minecraft:models/halo/model.obj") -> ObjMeshData:
    if len(text) > MAX_TEXT_LENGTH:
        raise ValueError(f"{resource_id}:1: OBJ exceeds 16 MiB text limit")
    positions: list[tuple[float, float, float]] = []
    uvs: list[tuple[float, float]] = []
    normal_count = 0
    vertices: dict[tuple[int, int], int] = {}
    corners: list[tuple[int, int]] = []
    triangles: list[tuple[int, int, int]] = []

    def problem(line_number: int, message: str) -> ValueError:
        return ValueError(f"{resource_id}:{line_number}: {message}")

    def validate_polygon(face: list[tuple[int, int]]) -> None:
        points = [positions[position] for position, _uv in face]
        normal = _cross(_sub(points[1], points[0]), _sub(points[2], points[0]))
        length = sqrt(_dot(normal, normal))
        extent = max(sqrt(_dot(_sub(point, points[0]), _sub(point, points[0]))) for point in points[1:])
        if length <= extent * extent * 1e-10:
            raise ValueError("Degenerate face")
        if len(points) == 3:
            return
        if abs(_dot(normal, _sub(points[3], points[0]))) > length * extent * 1e-5:
            raise ValueError("Non-planar quad; triangulate on export")
        for index in range(4):
            a = _sub(points[(index + 1) % 4], points[index])
            b = _sub(points[(index + 2) % 4], points[(index + 1) % 4])
            if _dot(_cross(a, b), normal) <= length * length * 1e-10:
                raise ValueError("Concave, crossing or degenerate quad; triangulate on export")

    logical_lines: list[tuple[int, str]] = []
    continued = ""
    first_line = 1
    for line_number, source_line in enumerate(text.splitlines(), 1):
        line = source_line[1:] if line_number == 1 and source_line.startswith("\ufeff") else source_line
        line = line.split("#", 1)[0].strip()
        if not continued:
            first_line = line_number
        if line.endswith("\\"):
            continued += line[:-1] + " "
            continue
        if continued:
            line = continued + line
            continued = ""
        logical_lines.append((first_line, line.strip()))
    if continued:
        raise problem(first_line, "Unterminated line continuation")

    for line_number, line in logical_lines:
        if not line:
            continue
        words = line.split()
        statement = words[0]
        try:
            if statement == "v":
                if len(words) not in {4, 5}:
                    raise ValueError("v requires x y z [w=1]")
                if len(words) == 5 and _finite(words[4]) != 1:
                    raise ValueError("Only polygonal v with w=1 is supported")
                positions.append(tuple(_finite(word) for word in words[1:4]))
            elif statement == "vt":
                if len(words) not in {3, 4}:
                    raise ValueError("vt requires u v [w=0]")
                if len(words) == 4 and _finite(words[3]) != 0:
                    raise ValueError("Only two-dimensional UVs are supported")
                u, v = _finite(words[1]), _float32(1.0 - _finite(words[2]))
                if not isfinite(v):
                    raise ValueError("Unrepresentable UV")
                uvs.append((u, v))
            elif statement == "vn":
                if len(words) != 4:
                    raise ValueError("vn requires x y z")
                tuple(_finite(word) for word in words[1:4])
                normal_count += 1
            elif statement == "f":
                count = len(words) - 1
                if count not in {3, 4}:
                    raise ValueError("Only triangles and planar convex quads are supported; triangulate on export")
                face = []
                for word in words[1:]:
                    reference = word.split("/")
                    if len(reference) not in {2, 3} or not reference[1]:
                        raise ValueError("Every face corner requires a UV (v/vt or v/vt/vn)")
                    position = _index(reference[0], len(positions))
                    uv = _index(reference[1], len(uvs))
                    if len(reference) == 3:
                        _index(reference[2], normal_count)
                    face.append((position, uv))
                validate_polygon(face)
                mapped = []
                for corner in face:
                    if corner not in vertices:
                        vertices[corner] = len(corners)
                        corners.append(corner)
                    mapped.append(vertices[corner])
                triangles.append((mapped[0], mapped[1], mapped[2]))
                if count == 4:
                    triangles.append((mapped[0], mapped[2], mapped[3]))
            elif statement in {"o", "g", "s", "usemtl", "mtllib"}:
                pass
            else:
                raise ValueError(f"Unsupported OBJ statement '{statement}'; export textured triangles")
            if len(positions) + len(uvs) + normal_count > MAX_ELEMENTS or len(triangles) > MAX_TRIANGLES:
                raise ValueError("OBJ exceeds geometry limits")
        except (TypeError, ValueError) as exc:
            raise problem(line_number, str(exc)) from exc

    if not triangles:
        raise problem(max(1, len(text.splitlines())), "OBJ contains no textured faces")
    packed_positions = tuple(positions[position] for position, _uv in corners)
    packed_uvs = tuple(uvs[uv] for _position, uv in corners)
    minimum = tuple(min(position[axis] for position in packed_positions) for axis in range(3))
    maximum = tuple(max(position[axis] for position in packed_positions) for axis in range(3))
    return ObjMeshData(packed_positions, packed_uvs, tuple(triangles), minimum, maximum)


def resolve_model_path(model_id: str, pack_root: str | Path | None) -> Path | None:
    if not model_id or not pack_root:
        return None
    namespace, relative = model_id.split(":", 1) if ":" in model_id else ("minecraft", model_id)
    relative_path = Path(*relative.replace("\\", "/").split("/"))
    if not namespace or relative_path.is_absolute() or ".." in relative_path.parts:
        return None
    root = Path(pack_root).resolve()
    candidate = (root / "assets" / namespace / relative_path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate if candidate.is_file() else None


def load_obj_resource(model_id: str, pack_root: str | Path | None) -> ObjMeshData:
    path = resolve_model_path(model_id, pack_root)
    if path is None:
        raise FileNotFoundError(f"缺少 OBJ：{model_id}")
    return parse_obj(path.read_text(encoding="utf-8-sig"), model_id)


def copy_obj_resource(source: str | Path, pack_root: str | Path, model_id: str) -> tuple[str, Path]:
    """Copy one validated OBJ into an editable pack, reusing identical data.

    A numeric suffix is only introduced when the requested resource path is
    already occupied by different OBJ bytes.  The returned identifier always
    reflects the actual destination.
    """

    model_id = lowercase_resource_identifier(model_id)
    source_path = Path(source).resolve()
    if source_path.suffix.lower() != ".obj" or not source_path.is_file():
        raise ValueError("请选择有效的 .obj 文件")
    text = source_path.read_text(encoding="utf-8-sig")
    parse_obj(text, model_id)
    namespace, relative = model_id.split(":", 1) if ":" in model_id else ("minecraft", model_id)
    if not relative.lower().endswith(".obj"):
        relative += ".obj"
    if (not re.fullmatch(r"[a-z0-9_.-]+", namespace)
            or not re.fullmatch(r"[a-z0-9_./-]+", relative)
            or ".." in Path(*relative.split("/")).parts):
        raise ValueError(f"无效的 OBJ 资源 ID：{namespace}:{relative}")
    root = Path(pack_root).resolve()
    namespace_root = (root / "assets" / namespace).resolve()
    destination = (namespace_root / Path(*relative.replace("\\", "/").split("/"))).resolve()
    try:
        destination.relative_to(namespace_root)
    except ValueError as exc:
        raise ValueError("OBJ 资源路径不能离开命名空间目录") from exc
    source_digest = hashlib.sha256(source_path.read_bytes()).digest()
    candidate = destination
    suffix = 1
    while candidate.exists():
        if candidate.is_file() and hashlib.sha256(candidate.read_bytes()).digest() == source_digest:
            actual_relative = candidate.relative_to(namespace_root).as_posix()
            return f"{namespace}:{actual_relative}", candidate
        candidate = destination.with_name(f"{destination.stem}_{suffix}{destination.suffix}")
        suffix += 1
    candidate.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_path, candidate)
    actual_relative = candidate.relative_to(namespace_root).as_posix()
    return f"{namespace}:{actual_relative}", candidate


def build_blender_mesh(
    name: str,
    data: ObjMeshData,
    size: Sequence[float] | None,
    *,
    preserve_proportions: bool = False,
    scale: float = 1.0,
):
    """Create a Blender mesh matching HaloCore's two scaling modes."""

    import bpy
    from .geometry import mc_to_blender

    uniform_scale = float(scale)
    if not isfinite(uniform_scale) or uniform_scale < 0:
        raise ValueError("mesh.scale 必须是有限非负数")
    if preserve_proportions:
        scales = [uniform_scale, uniform_scale, uniform_scale]
    else:
        if size is None:
            raise ValueError("未保持原始比例时必须提供 mesh.size")
        target = tuple(float(value) for value in size[:3])
        if len(target) != 3 or any(not isfinite(value) or value < 0 for value in target):
            raise ValueError("mesh.size 必须是三个有限非负数")
        scales = []
        for axis in range(3):
            extent = data.maximum[axis] - data.minimum[axis]
            if extent == 0:
                if target[axis] != 0:
                    raise ValueError(f"OBJ 第 {axis + 1} 轴范围为 0，因此对应 size 也必须为 0")
                scales.append(1.0)
            else:
                scales.append(target[axis] / extent)
    vertices = [mc_to_blender(tuple(position[axis] * scales[axis] for axis in range(3)))
                for position in data.positions]
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(vertices, [], data.triangles)
    uv_layer = mesh.uv_layers.new(name="UVMap")
    for polygon in mesh.polygons:
        for loop_index in polygon.loop_indices:
            vertex_index = mesh.loops[loop_index].vertex_index
            u, core_v = data.uvs[vertex_index]
            uv_layer.data[loop_index].uv = (u, 1.0 - core_v)
    mesh.validate(clean_customdata=False)
    mesh.update(calc_edges=True)
    mesh["halo_obj_vertex_count"] = data.vertex_count
    mesh["halo_obj_triangle_count"] = data.triangle_count
    mesh["halo_obj_bounds_min"] = data.minimum
    mesh["halo_obj_bounds_max"] = data.maximum
    return mesh


__all__ = [
    "MAX_TEXT_LENGTH", "MAX_ELEMENTS", "MAX_TRIANGLES", "ObjMeshData",
    "parse_obj", "resolve_model_path", "load_obj_resource", "copy_obj_resource", "build_blender_mesh",
]
