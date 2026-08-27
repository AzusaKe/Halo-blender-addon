"""Convert ordinary Blender mesh faces into Halo billboard group trees."""

from __future__ import annotations

import math
import re
import shutil
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path

try:
    import bpy
    from mathutils import Matrix, Vector
except ImportError:  # pragma: no cover - Blender-only conversion path
    bpy = None
    Matrix = Vector = None

from . import blender_scene
from .geometry import blender_rotation_to_mc_euler, blender_to_mc


@dataclass
class FaceRectangle:
    center: Vector
    axis_u: Vector
    axis_v: Vector
    normal: Vector
    width: float
    height: float
    uvs: list[tuple[float, float]]
    non_planar_error: float = 0.0


@dataclass
class MeshConversionResult:
    wrapper: object
    face_count: int
    texture_ids: list[str]
    texture_paths: list[str]
    warnings: list[str]


def _convex_hull(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    unique = sorted(set(points))
    if len(unique) <= 1:
        return unique

    def cross(origin, a, b):
        return (a[0] - origin[0]) * (b[1] - origin[1]) - (a[1] - origin[1]) * (b[0] - origin[0])

    lower: list[tuple[float, float]] = []
    for point in unique:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], point) <= 1.0e-12:
            lower.pop()
        lower.append(point)
    upper: list[tuple[float, float]] = []
    for point in reversed(unique):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], point) <= 1.0e-12:
            upper.pop()
        upper.append(point)
    return lower[:-1] + upper[:-1]


def minimum_face_rectangle(vertices, normal) -> FaceRectangle:
    """Return the minimum-area rectangle covering one polygon in its plane."""

    points_3d = [Vector(value) for value in vertices]
    if len(points_3d) < 3:
        raise ValueError("面至少需要三个顶点")
    normal = Vector(normal)
    if normal.length <= 1.0e-10:
        raise ValueError("面的法线长度为零")
    normal.normalize()
    origin = sum(points_3d, Vector()) / len(points_3d)
    reference = min(
        (Vector((1.0, 0.0, 0.0)), Vector((0.0, 1.0, 0.0)), Vector((0.0, 0.0, 1.0))),
        key=lambda axis: abs(axis.dot(normal)),
    )
    basis_u = reference - normal * reference.dot(normal)
    basis_u.normalize()
    basis_v = normal.cross(basis_u).normalized()
    projected = [((point - origin).dot(basis_u), (point - origin).dot(basis_v)) for point in points_3d]
    hull = _convex_hull(projected)
    if len(hull) < 3:
        raise ValueError("面的投影退化为直线")

    best = None
    for index, point in enumerate(hull):
        following = hull[(index + 1) % len(hull)]
        dx, dy = following[0] - point[0], following[1] - point[1]
        length = math.hypot(dx, dy)
        if length <= 1.0e-12:
            continue
        cos_a, sin_a = dx / length, dy / length
        rotated = [
            (x * cos_a + y * sin_a, -x * sin_a + y * cos_a)
            for x, y in projected
        ]
        min_u = min(value[0] for value in rotated)
        max_u = max(value[0] for value in rotated)
        min_v = min(value[1] for value in rotated)
        max_v = max(value[1] for value in rotated)
        width, height = max_u - min_u, max_v - min_v
        score = width * height
        if best is None or score < best[0] - 1.0e-12:
            best = (score, cos_a, sin_a, min_u, max_u, min_v, max_v, rotated)
    if best is None:
        raise ValueError("无法计算面的覆盖矩形")

    _score, cos_a, sin_a, min_u, max_u, min_v, max_v, rotated = best
    width, height = max_u - min_u, max_v - min_v
    if width <= 1.0e-8 or height <= 1.0e-8:
        raise ValueError("面的覆盖矩形尺寸为零")
    axis_u = (basis_u * cos_a + basis_v * sin_a).normalized()
    axis_v = (-basis_u * sin_a + basis_v * cos_a).normalized()
    center_u, center_v = (min_u + max_u) * 0.5, (min_v + max_v) * 0.5
    center = origin + axis_u * center_u + axis_v * center_v
    uvs = [((u - min_u) / width, (v - min_v) / height) for u, v in rotated]
    non_planar_error = max(abs((point - origin).dot(normal)) for point in points_3d)
    return FaceRectangle(center, axis_u, axis_v, normal, width, height, uvs, non_planar_error)


def _safe_resource_component(value: str, fallback: str = "mesh") -> str:
    result = re.sub(r"[^a-z0-9._-]+", "_", str(value or "").strip().lower()).strip("._-")
    return result or fallback


def _texture_size(rectangle: FaceRectangle, maximum: int) -> tuple[int, int]:
    maximum = max(16, min(4096, int(maximum)))
    longest = max(rectangle.width, rectangle.height)
    return (
        max(8, round(maximum * rectangle.width / longest)),
        max(8, round(maximum * rectangle.height / longest)),
    )


def _make_bake_material(source, source_uv_name: str | None):
    material = source.copy() if source is not None else bpy.data.materials.new("Halo Mesh Bake Default")
    material.use_nodes = True
    nodes = material.node_tree.nodes
    links = material.node_tree.links
    if source_uv_name:
        for node in list(nodes):
            if node.bl_idname != "ShaderNodeTexImage" or node.inputs.get("Vector") is None or node.inputs["Vector"].is_linked:
                continue
            uv_node = nodes.new("ShaderNodeUVMap")
            uv_node.uv_map = source_uv_name
            links.new(uv_node.outputs["UV"], node.inputs["Vector"])
    return material


def _opaque_polygon_mask(width: int, height: int, polygon_uvs: list[tuple[float, float]]) -> list[float]:
    """Rasterize a concave UV polygon for materials without Principled Alpha."""

    mask = [0.0] * (width * height)
    count = len(polygon_uvs)
    for y in range(height):
        py = (y + 0.5) / height
        for x in range(width):
            px = (x + 0.5) / width
            inside = False
            previous = count - 1
            for current in range(count):
                x1, y1 = polygon_uvs[current]
                x2, y2 = polygon_uvs[previous]
                if (y1 > py) != (y2 > py):
                    intersection = (x2 - x1) * (py - y1) / ((y2 - y1) or 1.0e-20) + x1
                    if px < intersection:
                        inside = not inside
                previous = current
            if inside:
                mask[y * width + x] = 1.0
    return mask


def _expand_polygon_border(
    pixels: list[float],
    polygon_mask: list[float],
    width: int,
    height: int,
    rings: int,
):
    """Expand a polygon by exact RGBA copies without averaging edge colors."""

    covered = [value > 0.5 for value in polygon_mask]
    neighbors = (
        (-1, -1), (0, -1), (1, -1),
        (-1, 0),            (1, 0),
        (-1, 1),  (0, 1),  (1, 1),
    )
    for _ring in range(max(0, int(rings))):
        previous_pixels = pixels[:]
        previous_covered = covered[:]
        additions: list[tuple[int, int]] = []
        for y in range(height):
            for x in range(width):
                index = y * width + x
                if previous_covered[index]:
                    continue
                source_index = None
                for dx, dy in neighbors:
                    nx, ny = x + dx, y + dy
                    if 0 <= nx < width and 0 <= ny < height:
                        neighbor_index = ny * width + nx
                        if previous_covered[neighbor_index]:
                            source_index = neighbor_index
                            break
                if source_index is not None:
                    additions.append((index, source_index))
        if not additions:
            break
        for index, source_index in additions:
            target_offset = index * 4
            source_offset = source_index * 4
            # Copy, rather than average or interpolate.  This preserves the
            # exact baked RGBA of the nearest sampled face pixel and prevents
            # transparent black from producing dark seams.
            pixels[target_offset:target_offset + 4] = previous_pixels[source_offset:source_offset + 4]
            covered[index] = True


def _active_output(nodes):
    return next(
        (node for node in nodes if node.bl_idname == "ShaderNodeOutputMaterial" and node.is_active_output),
        next((node for node in nodes if node.bl_idname == "ShaderNodeOutputMaterial"), None),
    )


def _bake_face_texture(
    context,
    source_obj,
    source_mesh,
    polygon,
    rectangle: FaceRectangle,
    destination: Path,
    texture_id: str,
    resolution: int,
    bake_mode: str,
    edge_padding: int,
):
    """Bake one polygon's color and Principled alpha to a transparent PNG."""

    token = uuid.uuid4().hex[:10]
    source_uv_layer = source_mesh.uv_layers.active if source_mesh.uv_layers else None
    source_uv_name = source_uv_layer.name if source_uv_layer is not None else None
    vertices = [source_mesh.vertices[index].co.copy() for index in polygon.vertices]
    temp_mesh = bpy.data.meshes.new(f"Halo Mesh Bake {token}")
    temp_mesh.from_pydata(vertices, [], [tuple(range(len(vertices)))])
    temp_mesh.update()
    copied_uv_layers = {}
    for source_layer in source_mesh.uv_layers:
        copied_layer = temp_mesh.uv_layers.new(name=source_layer.name)
        for target_loop, source_loop_index in zip(copied_layer.data, polygon.loop_indices):
            target_loop.uv = source_layer.data[source_loop_index].uv
        copied_uv_layers[source_layer.name] = copied_layer
    copied_uv = copied_uv_layers.get(source_uv_name)
    target_uv = temp_mesh.uv_layers.new(name="Halo Bake Target")
    for loop, uv in zip(target_uv.data, rectangle.uvs):
        loop.uv = uv
    # ShaderNodeTexCoord's generic UV output reads the active render layer.
    # Keep that pointing at the copied source UV so linked materials sample
    # exactly the source face's atlas region.  The bake destination is chosen
    # separately through bpy.ops.object.bake(uv_layer=...), otherwise every
    # face incorrectly remaps the complete source texture onto its rectangle.
    if copied_uv is not None:
        temp_mesh.uv_layers.active_index = 0
        copied_uv.active_render = True
    else:
        temp_mesh.uv_layers.active_index = len(temp_mesh.uv_layers) - 1
        target_uv.active_render = True

    temp_obj = bpy.data.objects.new(f"Halo Mesh Bake {token}", temp_mesh)
    context.scene.collection.objects.link(temp_obj)
    source_material = None
    if polygon.material_index < len(source_obj.material_slots):
        source_material = source_obj.material_slots[polygon.material_index].material
    bake_material = _make_bake_material(source_material, source_uv_name)
    temp_mesh.materials.append(bake_material)
    width, height = _texture_size(rectangle, resolution)
    color_image = bpy.data.images.new(f"Halo Mesh Face {token}", width=width, height=height, alpha=True)
    color_image.generated_color = (0.0, 0.0, 0.0, 0.0)
    alpha_image = bpy.data.images.new(f"Halo Mesh Alpha {token}", width=width, height=height, alpha=True)
    alpha_image.generated_color = (0.0, 0.0, 0.0, 1.0)
    nodes = bake_material.node_tree.nodes
    links = bake_material.node_tree.links
    target_node = nodes.new("ShaderNodeTexImage")
    target_node.name = "Halo Mesh Bake Target"
    target_node.image = color_image

    previous_engine = context.scene.render.engine
    previous_active = context.view_layer.objects.active
    previous_selected = list(context.selected_objects)
    success = False
    try:
        for obj in previous_selected:
            obj.select_set(False)
        temp_obj.select_set(True)
        context.view_layer.objects.active = temp_obj
        context.scene.render.engine = "CYCLES"
        for node in nodes:
            node.select = False
        target_node.select = True
        nodes.active = target_node
        output_before_alpha = _active_output(nodes)
        surface_source = (
            output_before_alpha.inputs["Surface"].links[0].from_node
            if output_before_alpha is not None and output_before_alpha.inputs["Surface"].is_linked
            else None
        )
        selected_mode = str(bake_mode or "AUTO").upper()
        if selected_mode == "AUTO":
            if surface_source is not None and surface_source.bl_idname == "ShaderNodeEmission":
                selected_mode = "EMIT"
            elif surface_source is not None and surface_source.bl_idname == "ShaderNodeBsdfPrincipled":
                selected_mode = "DIFFUSE"
            else:
                selected_mode = "COMBINED"
        if selected_mode == "DIFFUSE":
            bpy.ops.object.bake(
                type="DIFFUSE",
                pass_filter={"COLOR"},
                margin=0,
                use_clear=True,
                uv_layer=target_uv.name,
            )
        else:
            bpy.ops.object.bake(type=selected_mode, margin=0, use_clear=True, uv_layer=target_uv.name)

        output = _active_output(nodes)
        principled = surface_source if surface_source is not None and surface_source.bl_idname == "ShaderNodeBsdfPrincipled" else None
        polygon_mask = _opaque_polygon_mask(width, height, rectangle.uvs)
        if output is not None and principled is not None:
            target_node.image = alpha_image
            alpha_socket = principled.inputs.get("Alpha")
            emission = nodes.new("ShaderNodeEmission")
            emission.inputs["Strength"].default_value = 1.0
            if alpha_socket is not None and alpha_socket.is_linked:
                links.new(alpha_socket.links[0].from_socket, emission.inputs["Color"])
            else:
                alpha_value = float(alpha_socket.default_value) if alpha_socket is not None else 1.0
                emission.inputs["Color"].default_value = (alpha_value, alpha_value, alpha_value, 1.0)
            for link in list(output.inputs["Surface"].links):
                links.remove(link)
            links.new(emission.outputs["Emission"], output.inputs["Surface"])
            bpy.ops.object.bake(type="EMIT", margin=0, use_clear=True, uv_layer=target_uv.name)
            color_pixels = list(color_image.pixels)
            alpha_pixels = list(alpha_image.pixels)
            if len(alpha_pixels) != width * height * 4:
                raise RuntimeError("Alpha 烘焙图像像素格式异常")
            for offset in range(0, len(color_pixels), 4):
                color_pixels[offset + 3] = max(0.0, min(1.0, alpha_pixels[offset]))
        else:
            color_pixels = list(color_image.pixels)
            for index, alpha_value in enumerate(polygon_mask):
                color_pixels[index * 4 + 3] = alpha_value
        _expand_polygon_border(color_pixels, polygon_mask, width, height, edge_padding)
        color_image.pixels = color_pixels

        destination.parent.mkdir(parents=True, exist_ok=True)
        color_image.filepath_raw = str(destination)
        color_image.file_format = "PNG"
        color_image.save()
        color_image["halo_texture_id"] = texture_id
        color_image["halo_source_path"] = str(destination)
        success = True
        return color_image
    finally:
        context.scene.render.engine = previous_engine
        if temp_obj.name in bpy.data.objects:
            bpy.data.objects.remove(temp_obj, do_unlink=True)
        if temp_mesh.users == 0:
            bpy.data.meshes.remove(temp_mesh)
        if bake_material.users == 0:
            bpy.data.materials.remove(bake_material)
        if alpha_image.users == 0:
            bpy.data.images.remove(alpha_image)
        for obj in previous_selected:
            if obj.name in bpy.data.objects:
                obj.select_set(True)
        if previous_active is not None and previous_active.name in bpy.data.objects:
            context.view_layer.objects.active = previous_active
        if not success and color_image.users == 0:
            bpy.data.images.remove(color_image)


def _unique_group_id(definition_id: str, base: str, reserved: set[str]) -> str:
    base = str(base or "mesh").strip() or "mesh"
    used = reserved | {
        str(getattr(getattr(obj, "halo_node", None), "node_id", "") or "")
        for obj in bpy.data.objects
        if obj.get("halo_role") == blender_scene.GROUP_ROLE and obj.get("halo_definition_id") == definition_id
    }
    candidate = base
    index = 2
    while candidate in used:
        candidate = f"{base}_{index}"
        index += 1
    reserved.add(candidate)
    return candidate


def convert_mesh_to_halo(
    context,
    source_obj,
    parent,
    *,
    texture_resolution: int = 256,
    apply_modifiers: bool = True,
    bake_mode: str = "AUTO",
    edge_padding: int = 2,
):
    """Bake and convert all usable source faces under a new Halo wrapper group."""

    if source_obj is None or source_obj.type != "MESH":
        raise ValueError("请选择一个网格对象")
    if source_obj.get("halo_role"):
        raise ValueError("不能把 Halo 自身的预览图元再次转换")
    if parent is None or parent.get("halo_role") not in {blender_scene.ROOT_ROLE, blender_scene.GROUP_ROLE}:
        raise ValueError("请选择光环根或部件组作为目标父级")
    project = context.scene.halo_project
    pack_root = str(project.pack_root or "")
    if not pack_root:
        raise ValueError("当前项目没有资源包工作目录")
    definition_id = str(parent.get("halo_definition_id", ""))
    namespace = definition_id.split(":", 1)[0] if ":" in definition_id else "minecraft"
    token = uuid.uuid4().hex[:8]
    resource_folder = f"textures/halo/mesh_bakes/{_safe_resource_component(source_obj.name)}_{token}"
    bake_root = Path(tempfile.mkdtemp(prefix="halo_mesh_bakes_"))

    evaluated_obj = source_obj.evaluated_get(context.evaluated_depsgraph_get()) if apply_modifiers else source_obj
    mesh = evaluated_obj.to_mesh(preserve_all_data_layers=True, depsgraph=context.evaluated_depsgraph_get()) if apply_modifiers else source_obj.data
    baked: list[tuple[object, FaceRectangle, str, str]] = []
    warnings: list[str] = []
    created_paths: list[str] = []
    created_images: list[object] = []
    wrapper = None
    context.window_manager.progress_begin(0, max(1, len(mesh.polygons)))
    try:
        for progress, polygon in enumerate(mesh.polygons, 1):
            context.window_manager.progress_update(progress)
            try:
                rectangle = minimum_face_rectangle([mesh.vertices[index].co for index in polygon.vertices], polygon.normal)
            except ValueError as exc:
                warnings.append(f"面 {polygon.index}: {exc}，已跳过")
                continue
            if rectangle.non_planar_error > max(rectangle.width, rectangle.height) * 1.0e-4:
                warnings.append(f"面 {polygon.index}: 非平面误差 {rectangle.non_planar_error:.6g}，已投影到拟合平面")
            texture_id = f"{namespace}:{resource_folder}/face_{polygon.index:04d}.png"
            destination = bake_root / f"face_{polygon.index:04d}.png"
            image = _bake_face_texture(
                context,
                evaluated_obj,
                mesh,
                polygon,
                rectangle,
                destination,
                texture_id,
                texture_resolution,
                bake_mode,
                edge_padding,
            )
            image["halo_generated_texture"] = True
            image.pack()
            image.filepath_raw = ""
            created_images.append(image)
            baked.append((polygon, rectangle, texture_id, str(destination)))
            created_paths.append(str(destination))
        if not baked:
            raise ValueError("网格没有可转换的有效面")

        collection = parent.users_collection[0] if parent.users_collection else blender_scene._ensure_collection(context.scene)
        reserved: set[str] = set()
        wrapper_id = _unique_group_id(definition_id, source_obj.name, reserved)
        wrapper_raw = {
            "id": wrapper_id,
            "position": [0.0, 0.0, 0.0],
            "rotation": [0.0, 0.0, 0.0],
            "scale": 1.0,
            "animation": {},
            "children": [],
        }
        wrapper = blender_scene._make_group(collection, parent, wrapper_raw, definition_id, f"mesh/{token}")
        wrapper.name = source_obj.name
        for face_number, (_polygon, rectangle, texture_id, _destination) in enumerate(baked):
            face_id = _unique_group_id(definition_id, f"{wrapper_id}_face_{face_number + 1}", reserved)
            # Billboard local X follows the rectangle U axis.  Local Y follows
            # -V so its authored -Z normal points along the source face normal.
            rotation_matrix = Matrix((rectangle.axis_u, -rectangle.axis_v, -rectangle.normal)).transposed()
            face_raw = {
                "id": face_id,
                "position": list(blender_to_mc(rectangle.center)),
                "rotation": list(blender_rotation_to_mc_euler(rotation_matrix)),
                "scale": 1.0,
                "animation": {},
                "children": [],
            }
            face_group = blender_scene._make_group(
                collection,
                wrapper,
                face_raw,
                definition_id,
                f"mesh/{token}/face/{face_number}",
            )
            primitive = {
                "type": "billboard",
                "texture": texture_id,
                "size": [rectangle.width, rectangle.height],
                "face_camera": False,
            }
            blender_scene._make_primitive(
                collection,
                face_group,
                primitive,
                definition_id,
                f"mesh/{token}/face/{face_number}/primitive/0",
                True,
            )
        blender_scene.sync_definition_from_scene(context.scene, definition_id)
        export_paths = []
        for _polygon, _rectangle, texture_id, _destination in baked:
            texture_namespace, texture_relative = texture_id.split(":", 1)
            export_paths.append(f"assets/{texture_namespace}/{texture_relative}")
        return MeshConversionResult(wrapper, len(baked), [item[2] for item in baked], export_paths, warnings)
    except Exception:
        if wrapper is not None and wrapper.name in bpy.data.objects:
            objects_to_remove = []
            stack = [wrapper]
            while stack:
                current = stack.pop()
                objects_to_remove.append(current)
                stack.extend(current.children)
            meshes_to_remove = [obj.data for obj in objects_to_remove if getattr(obj, "data", None) is not None]
            materials_to_remove = [
                material for mesh_data in meshes_to_remove
                for material in getattr(mesh_data, "materials", ()) if material is not None
            ]
            blender_scene.remove_object_tree(wrapper)
            for mesh_data in meshes_to_remove:
                if mesh_data.users == 0:
                    bpy.data.meshes.remove(mesh_data)
            for material in materials_to_remove:
                if material.users == 0:
                    bpy.data.materials.remove(material)
        for path in created_paths:
            candidate = Path(path)
            if candidate.is_file():
                candidate.unlink()
        for image in created_images:
            if image.name in bpy.data.images and image.users == 0:
                bpy.data.images.remove(image)
        raise
    finally:
        context.window_manager.progress_end()
        shutil.rmtree(bake_root, ignore_errors=True)
        if apply_modifiers:
            evaluated_obj.to_mesh_clear()


__all__ = [
    "FaceRectangle",
    "MeshConversionResult",
    "minimum_face_rectangle",
    "convert_mesh_to_halo",
]
