"""Geometry and coordinate helpers for the Halo Pack Editor.

The Minecraft renderer uses ``(x, y, z)`` with a halo lying on the XZ
plane.  Blender's conventional Z-up coordinates are deliberately kept in
the scene by using the one, explicit conversion in this module::

    minecraft (x, y, z) -> blender (x, -z, y)

Keeping mesh generation here (instead of making the operators know about
the primitive format) also makes the importer useful from background mode
and from tests that provide a small ``bpy`` stub.
"""

from __future__ import annotations

import math
from typing import Iterable, Sequence

try:  # Blender supplies mathutils.  The fallback keeps static tooling usable.
    from mathutils import Matrix, Quaternion, Vector
except ImportError:  # pragma: no cover - only used outside Blender
    Matrix = Quaternion = Vector = None  # type: ignore


def mc_to_blender(value: Sequence[float] | Iterable[float]) -> tuple[float, float, float]:
    """Convert a point/vector from Halo/Minecraft coordinates to Blender."""

    x, y, z = (float(v) for v in value)
    return x, -z, y


def blender_to_mc(value: Sequence[float] | Iterable[float]) -> tuple[float, float, float]:
    """Convert a point/vector from Blender back to Halo/Minecraft space."""

    x, y, z = (float(v) for v in value)
    return x, z, -y


def _axis_matrix():
    if Matrix is None:  # pragma: no cover
        return None
    # b = S*m.  S is orthonormal and therefore its inverse is its transpose.
    return Matrix(((1.0, 0.0, 0.0), (0.0, 0.0, -1.0), (0.0, 1.0, 0.0)))


def mc_rotation_matrix(rotation: Sequence[float] | Iterable[float]):
    """Return a Blender-space 3x3 matrix for a Halo YXZ Euler rotation.

    JSON stores ``[yaw, pitch, roll]`` in degrees and the Java loader applies
    ``rotateY(yaw).rotateX(pitch).rotateZ(roll)``.  Constructing the matrix in
    Minecraft space and changing basis avoids Blender Euler-order surprises.
    """

    yaw, pitch, roll = (math.radians(float(v)) for v in rotation)
    if Matrix is None:  # pragma: no cover
        return None
    mc = Matrix.Identity(3)
    mc = mc @ Matrix.Rotation(yaw, 3, "Y")
    mc = mc @ Matrix.Rotation(pitch, 3, "X")
    mc = mc @ Matrix.Rotation(roll, 3, "Z")
    axis = _axis_matrix()
    return axis @ mc @ axis.transposed()


def mc_rotation_quaternion(rotation: Sequence[float] | Iterable[float]):
    """Return a Blender quaternion for a Halo YXZ degree triplet."""

    matrix = mc_rotation_matrix(rotation)
    return matrix.to_quaternion() if matrix is not None else None


def blender_rotation_to_mc_euler(rotation) -> tuple[float, float, float]:
    """Convert a Blender rotation matrix/quaternion to JSON YXZ degrees."""

    if Matrix is None:  # pragma: no cover
        return 0.0, 0.0, 0.0
    matrix = rotation.to_matrix() if hasattr(rotation, "to_matrix") else rotation
    axis = _axis_matrix()
    mc_matrix = axis.transposed() @ matrix.to_3x3() @ axis
    # Invert the exact Java composition used by ``mc_rotation_matrix``:
    # ``Ry(yaw) @ Rx(pitch) @ Rz(roll)``.  mathutils' generic ``to_euler``
    # convention is not the inverse of that authored order for arbitrary
    # orientations (vertical Mesh faces exposed the discrepancy).
    sin_pitch = max(-1.0, min(1.0, -float(mc_matrix[1][2])))
    pitch = math.asin(sin_pitch)
    cos_pitch = math.cos(pitch)
    if abs(cos_pitch) > 1.0e-7:
        yaw = math.atan2(float(mc_matrix[0][2]), float(mc_matrix[2][2]))
        roll = math.atan2(float(mc_matrix[1][0]), float(mc_matrix[1][1]))
    else:
        # At pitch ±90° yaw and roll are coupled.  Choose roll=0 and preserve
        # the represented matrix through the remaining yaw degree of freedom.
        yaw = math.atan2(-float(mc_matrix[2][0]), float(mc_matrix[0][0]))
        roll = 0.0
    return math.degrees(yaw), math.degrees(pitch), math.degrees(roll)


def uniform_scale(value: Sequence[float] | Iterable[float], tolerance: float = 1e-5) -> tuple[float, bool]:
    """Return a representative scalar and whether a scale is uniform."""

    values = [float(v) for v in value]
    if not values:
        return 1.0, True
    result = sum(values) / len(values)
    return result, max(values) - min(values) <= tolerance


def _mesh_from_pydata(name: str, vertices, faces, uvs=None, material_indices=None):
    """Create a Blender mesh without requiring operators or UI context."""

    import bpy

    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(vertices, [], faces)
    mesh.update(calc_edges=True)

    if uvs:
        layer = mesh.uv_layers.new(name="UVMap")
        for loop, uv in zip(mesh.loops, uvs):
            layer.data[loop.index].uv = uv
    if material_indices:
        for poly, index in zip(mesh.polygons, material_indices):
            poly.material_index = int(index)
    return mesh


def billboard_mesh(name: str, size: Sequence[float] | Iterable[float]):
    """Build the same horizontal quad as ``BillboardPrimitive``.

    The mesh is authored in Minecraft coordinates first, then converted to
    Blender.  UVs match the game's corner order and its normal is ``-Y`` in
    definition space (``-Z`` after conversion).
    """

    width, depth = (float(v) for v in size)
    mc_vertices = [
        (-width / 2.0, 0.0, -depth / 2.0),
        (width / 2.0, 0.0, -depth / 2.0),
        (width / 2.0, 0.0, depth / 2.0),
        (-width / 2.0, 0.0, depth / 2.0),
    ]
    vertices = [mc_to_blender(v) for v in mc_vertices]
    # This winding produces -Y in MC space and -Z in Blender space.
    faces = [(0, 1, 2, 3)]
    # Blender's image V axis is opposite to Minecraft's texture sampling
    # convention.  Flip V on import so the image matches the in-game result.
    uvs = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
    return _mesh_from_pydata(name, vertices, faces, uvs)


def ring_mesh(
    name: str,
    size: Sequence[float] | Iterable[float],
    segments: int = 32,
    with_inner: bool = True,
):
    """Build the textured cylinder used by ``RingPrimitive``.

    ``size[0]`` is the radius and ``size[1]`` is the cylinder width along
    Minecraft Y.  U starts at +X and wraps once; V goes top-to-bottom.
    Outer and inner faces are separate material slots (0 and 1).
    """

    radius, width = (float(v) for v in size)
    segments = max(3, int(segments or 32))
    mc_vertices = []
    vertices = []
    for inner in ((False, True) if with_inner else (False,)):
        # ``width`` is the axial cylinder width, not radial thickness.  The
        # inner and outer surfaces therefore share the configured radius;
        # this is the representation used by RingPrimitive in the mod.
        r = radius
        for i in range(segments):
            theta = 2.0 * math.pi * i / segments
            c, s = math.cos(theta), math.sin(theta)
            mc_vertices.extend(((r * c, width / 2.0, r * s), (r * c, -width / 2.0, r * s)))
    vertices = [mc_to_blender(v) for v in mc_vertices]
    faces = []
    uvs = []
    material_indices = []
    # Outer surface.  The last segment is explicitly connected to segment 0
    # so U=0 is always the +X seam, including for odd segment counts.
    for i in range(segments):
        j = (i + 1) % segments
        a, b, c, d = 2 * i, 2 * j, 2 * j + 1, 2 * i + 1
        faces.append((a, b, c, d))
        u0, u1 = i / segments, (i + 1) / segments
        uvs.extend(((u0, 1.0), (u1, 1.0), (u1, 0.0), (u0, 0.0)))
        material_indices.append(0)
    if with_inner:
        offset = segments * 2
        for i in range(segments):
            j = (i + 1) % segments
            # Reverse winding for the inward-facing surface.
            a, b, c, d = offset + 2 * i, offset + 2 * i + 1, offset + 2 * j + 1, offset + 2 * j
            faces.append((a, b, c, d))
            u0, u1 = i / segments, (i + 1) / segments
            uvs.extend(((u0, 1.0), (u0, 0.0), (u1, 0.0), (u1, 1.0)))
            material_indices.append(1)
    return _mesh_from_pydata(name, vertices, faces, uvs, material_indices)


def head_anchor_matrix(
    offset: Sequence[float] | Iterable[float] = (0.0, 0.0, 0.0),
    rotation: Sequence[float] | Iterable[float] = (0.0, 0.0, 0.0),
):
    """Return a standard standing-player head anchor in Blender space.

    Minecraft's player head origin is approximately 1.62 blocks above the
    feet.  The anchor is intentionally a pure preview aid; it does not try to
    model entity poses or physics.
    """

    if Matrix is None:  # pragma: no cover
        return None
    anchor = mc_to_blender((float(offset[0]), 1.62 + float(offset[1]), float(offset[2])))
    result = Matrix.Translation(anchor)
    quat = mc_rotation_quaternion(rotation)
    if quat is not None:
        result = result @ quat.to_matrix().to_4x4()
    return result


def create_head_preview(name: str = "MC Player Head Preview"):
    """Create a 8x8x8 pixel (=0.5 block) transparent preview cube."""

    import bpy

    bpy.ops.mesh.primitive_cube_add(size=0.5, location=(0.0, 0.0, 0.0))
    obj = bpy.context.object
    obj.name = name
    obj["halo_role"] = "head_preview"
    obj["halo_preview_only"] = True
    return obj


__all__ = [
    "mc_to_blender",
    "blender_to_mc",
    "mc_rotation_matrix",
    "mc_rotation_quaternion",
    "blender_rotation_to_mc_euler",
    "uniform_scale",
    "billboard_mesh",
    "ring_mesh",
    "head_anchor_matrix",
    "create_head_preview",
]
