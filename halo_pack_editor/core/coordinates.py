"""Minecraft-to-Blender coordinate and transform helpers.

The game definition uses ``(x, y, z)`` with Y up.  The extension uses the
Blender convention requested by the editor: ``(x, -z, y)``.  All functions
return ordinary tuples and matrices, so this module can be tested without a
Blender installation.
"""

from __future__ import annotations

from math import atan2, asin, cos, degrees, hypot, pi, sin, sqrt
from typing import Iterable, Sequence

from .models import Vec3, as_vec3


Matrix4 = tuple[tuple[float, float, float, float], ...]
Matrix3 = tuple[tuple[float, float, float], ...]
Quaternion = tuple[float, float, float, float]  # (w, x, y, z)


IDENTITY4: Matrix4 = (
    (1.0, 0.0, 0.0, 0.0),
    (0.0, 1.0, 0.0, 0.0),
    (0.0, 0.0, 1.0, 0.0),
    (0.0, 0.0, 0.0, 1.0),
)

# Basis change: Blender x = MC x, Blender y = -MC z, Blender z = MC y.
MC_TO_BLENDER_BASIS: Matrix3 = (
    (1.0, 0.0, 0.0),
    (0.0, 0.0, -1.0),
    (0.0, 1.0, 0.0),
)
BLENDER_TO_MC_BASIS: Matrix3 = (
    (1.0, 0.0, 0.0),
    (0.0, 0.0, 1.0),
    (0.0, -1.0, 0.0),
)


def mc_to_blender(vector: Sequence[float]) -> Vec3:
    """Convert a Minecraft vector ``(x, y, z)`` to Blender ``(x, -z, y)``."""

    x, y, z = as_vec3(vector)
    return (x, -z, y)


def blender_to_mc(vector: Sequence[float]) -> Vec3:
    """Convert a Blender vector back to Minecraft coordinates."""

    x, y, z = as_vec3(vector)
    return (x, z, -y)


# Descriptive aliases used by import/export adapters.
minecraft_to_blender = mc_to_blender
blender_to_minecraft = blender_to_mc
mc_to_blender_vector = mc_to_blender
blender_to_mc_vector = blender_to_mc


def mat4_identity() -> Matrix4:
    return IDENTITY4


def mat4_multiply(a: Matrix4, b: Matrix4) -> Matrix4:
    return tuple(tuple(sum(a[i][k] * b[k][j] for k in range(4)) for j in range(4)) for i in range(4))  # type: ignore[return-value]


def mat3_multiply(a: Matrix3, b: Matrix3) -> Matrix3:
    return tuple(tuple(sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)) for i in range(3))  # type: ignore[return-value]


def mat3_transpose(a: Matrix3) -> Matrix3:
    return tuple(tuple(a[j][i] for j in range(3)) for i in range(3))  # type: ignore[return-value]


def mat4_from_mat3(a: Matrix3) -> Matrix4:
    return (
        (a[0][0], a[0][1], a[0][2], 0.0),
        (a[1][0], a[1][1], a[1][2], 0.0),
        (a[2][0], a[2][1], a[2][2], 0.0),
        (0.0, 0.0, 0.0, 1.0),
    )


def mat4_translation(vector: Sequence[float]) -> Matrix4:
    x, y, z = as_vec3(vector)
    return ((1.0, 0.0, 0.0, x), (0.0, 1.0, 0.0, y), (0.0, 0.0, 1.0, z), (0.0, 0.0, 0.0, 1.0))


def mat4_scale(scale: float | Sequence[float]) -> Matrix4:
    if isinstance(scale, (int, float)):
        x = y = z = float(scale)
    else:
        x, y, z = as_vec3(scale, (1.0, 1.0, 1.0))
    return ((x, 0.0, 0.0, 0.0), (0.0, y, 0.0, 0.0), (0.0, 0.0, z, 0.0), (0.0, 0.0, 0.0, 1.0))


def mat4_transform_point(matrix: Matrix4, point: Sequence[float]) -> Vec3:
    x, y, z = as_vec3(point)
    values = [x, y, z, 1.0]
    result = [sum(matrix[i][j] * values[j] for j in range(4)) for i in range(4)]
    w = result[3]
    if abs(w) > 1e-12 and abs(w - 1.0) > 1e-12:
        return (result[0] / w, result[1] / w, result[2] / w)
    return (result[0], result[1], result[2])


def mat4_transform_direction(matrix: Matrix4, direction: Sequence[float]) -> Vec3:
    x, y, z = as_vec3(direction)
    return (
        matrix[0][0] * x + matrix[0][1] * y + matrix[0][2] * z,
        matrix[1][0] * x + matrix[1][1] * y + matrix[1][2] * z,
        matrix[2][0] * x + matrix[2][1] * y + matrix[2][2] * z,
    )


def quat_identity() -> Quaternion:
    return (1.0, 0.0, 0.0, 0.0)


def quat_multiply(a: Quaternion, b: Quaternion) -> Quaternion:
    aw, ax, ay, az = a
    bw, bx, by, bz = b
    return (
        aw * bw - ax * bx - ay * by - az * bz,
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
    )


def quat_normalize(q: Quaternion) -> Quaternion:
    length = sqrt(sum(v * v for v in q))
    if length <= 1e-12:
        return quat_identity()
    return tuple(v / length for v in q)  # type: ignore[return-value]


def quat_from_axis_angle(axis: str, radians: float) -> Quaternion:
    half = radians * 0.5
    s = sin(half)
    if axis.lower() == "x":
        return (cos(half), s, 0.0, 0.0)
    if axis.lower() == "y":
        return (cos(half), 0.0, s, 0.0)
    if axis.lower() == "z":
        return (cos(half), 0.0, 0.0, s)
    raise ValueError(f"unknown rotation axis: {axis!r}")


def quaternion_from_yxz_degrees(rotation: Sequence[float]) -> Quaternion:
    """Build the same Y-X-Z quaternion used by the Halo Java parser."""

    yaw, pitch, roll = as_vec3(rotation)
    q = quat_multiply(
        quat_multiply(quat_from_axis_angle("y", yaw * pi / 180.0), quat_from_axis_angle("x", pitch * pi / 180.0)),
        quat_from_axis_angle("z", roll * pi / 180.0),
    )
    return quat_normalize(q)


quat_from_yxz_degrees = quaternion_from_yxz_degrees


def matrix3_from_quaternion(q: Quaternion) -> Matrix3:
    w, x, y, z = quat_normalize(q)
    return (
        (1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)),
        (2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)),
        (2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)),
    )


def matrix4_from_quaternion(q: Quaternion) -> Matrix4:
    return mat4_from_mat3(matrix3_from_quaternion(q))


def euler_yxz_matrix(rotation_degrees: Sequence[float]) -> Matrix3:
    return matrix3_from_quaternion(quaternion_from_yxz_degrees(rotation_degrees))


def minecraft_transform_matrix(
    position: Sequence[float] = (0.0, 0.0, 0.0),
    rotation_degrees: Sequence[float] = (0.0, 0.0, 0.0),
    scale: float | Sequence[float] = 1.0,
) -> Matrix4:
    """Compose a Minecraft-space ``T * R(YXZ) * S`` matrix."""

    return mat4_multiply(mat4_translation(position), mat4_multiply(matrix4_from_quaternion(quaternion_from_yxz_degrees(rotation_degrees)), mat4_scale(scale)))


def mc_to_blender_matrix(matrix: Matrix4) -> Matrix4:
    """Conjugate a Minecraft transform by the coordinate basis change."""

    change = mat4_from_mat3(MC_TO_BLENDER_BASIS)
    inverse = mat4_from_mat3(BLENDER_TO_MC_BASIS)
    return mat4_multiply(change, mat4_multiply(matrix, inverse))


def blender_to_mc_matrix(matrix: Matrix4) -> Matrix4:
    change = mat4_from_mat3(BLENDER_TO_MC_BASIS)
    inverse = mat4_from_mat3(MC_TO_BLENDER_BASIS)
    return mat4_multiply(change, mat4_multiply(matrix, inverse))


def compose_blender_transform(
    position: Sequence[float] = (0.0, 0.0, 0.0),
    rotation_degrees: Sequence[float] = (0.0, 0.0, 0.0),
    scale: float | Sequence[float] = 1.0,
) -> Matrix4:
    """Compose a Halo transform and return its Blender-space matrix."""

    return mc_to_blender_matrix(minecraft_transform_matrix(position, rotation_degrees, scale))


def blender_matrix_to_mc(matrix: Matrix4) -> Matrix4:
    return blender_to_mc_matrix(matrix)


def euler_yxz_from_matrix(matrix: Matrix3 | Matrix4) -> Vec3:
    """Extract Y-X-Z Euler degrees from a rotation matrix.

    This is mainly for synchronising Blender's transform panel back to JSON.
    At gimbal lock the returned yaw/roll pair is one valid solution; matrix
    round-tripping remains exact, which is the important property for export.
    """

    m = matrix
    if len(m) == 4:
        r = ((m[0][0], m[0][1], m[0][2]), (m[1][0], m[1][1], m[1][2]), (m[2][0], m[2][1], m[2][2]))
    else:
        r = matrix  # type: ignore[assignment]
    # For R = Ry(yaw) Rx(pitch) Rz(roll), r[1][2] = -sin(pitch).
    sp = max(-1.0, min(1.0, -r[1][2]))
    pitch = asin(sp)
    cp = cos(pitch)
    if abs(cp) > 1e-8:
        yaw = atan2(r[0][2], r[2][2])
        roll = atan2(r[1][0], r[1][1])
    else:
        # Gimbal lock: choose roll = 0 and solve yaw from the remaining terms.
        roll = 0.0
        yaw = atan2(-r[2][0], r[0][0])
    return (degrees(yaw), degrees(pitch), degrees(roll))


def mc_rotation_to_blender_matrix(rotation_degrees: Sequence[float]) -> Matrix3:
    """Return the Blender-space 3x3 rotation for MC YXZ Euler degrees."""

    converted = mc_to_blender_matrix(mat4_from_mat3(euler_yxz_matrix(rotation_degrees)))
    return (
        (converted[0][0], converted[0][1], converted[0][2]),
        (converted[1][0], converted[1][1], converted[1][2]),
        (converted[2][0], converted[2][1], converted[2][2]),
    )


def almost_equal_matrix(a: Matrix4, b: Matrix4, epsilon: float = 1e-6) -> bool:
    return all(abs(a[i][j] - b[i][j]) <= epsilon for i in range(4) for j in range(4))


def flatten_matrix(matrix: Matrix4) -> tuple[float, ...]:
    return tuple(value for row in matrix for value in row)
