"""Material and texture helpers.

Halo packs use ordinary PNG resources (including optional labPBR ``_n``,
``_s`` and ``_e`` companions).  This module keeps resource-id resolution and
Blender shader setup in one place so both import and export operators behave
the same way.
"""

from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path
from typing import Iterable

from .core.resource_paths import lowercase_resource_identifier
from .core.validation import has_integral_texture_scale


def split_resource_id(resource_id: str, default_namespace: str = "minecraft") -> tuple[str, str]:
    """Return ``(namespace, path)`` for a Minecraft resource identifier."""

    value = str(resource_id or "").strip().replace("\\", "/")
    if ":" in value:
        namespace, path = value.split(":", 1)
    else:
        namespace, path = default_namespace, value
    return namespace or default_namespace, path.lstrip("/")


def resolve_texture_path(texture_id: str, pack_root: str | os.PathLike[str] | None) -> str | None:
    """Resolve an identifier inside a pack without allowing path traversal."""

    if not pack_root:
        return None
    namespace, relative = split_resource_id(texture_id)
    root = Path(pack_root).resolve()
    candidate = (root / "assets" / namespace / relative).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    if candidate.is_file():
        return str(candidate)
    # A few hand-authored packs omit the extension.  The game ultimately
    # expects PNG, so accepting it here is friendly while retaining strict
    # path validation.
    if not candidate.suffix:
        png = candidate.with_suffix(".png")
        if png.is_file():
            return str(png)
    return None


def _set_socket(node, names: Iterable[str], value):
    for name in names:
        socket = node.inputs.get(name)
        if socket is not None:
            socket.default_value = value
            return socket
    return None


def _set_blend_mode(material):
    # Blender 5.2's DITHERED method uses hashed per-pixel transparency and
    # avoids BLENDED's object-order sorting failure.  Do not write the legacy
    # blend_method after surface_render_method: BLEND maps back to BLENDED and
    # silently undoes the setting above.
    configured = False
    if hasattr(material, "surface_render_method"):
        try:
            material.surface_render_method = "DITHERED"
            configured = True
        except (TypeError, ValueError):
            pass
    if not configured and hasattr(material, "blend_method"):
        try:
            material.blend_method = "HASHED"
        except (TypeError, ValueError):
            try:
                material.blend_method = "BLEND"
            except (TypeError, ValueError):
                pass
    if hasattr(material, "use_transparency_overlap"):
        material.use_transparency_overlap = False
    material["halo_preview_render_method"] = "EEVEE_DITHERED"


_BACKFACE_NODE_NAMES = (
    "Halo Backface Geometry",
    "Halo Backface Transparent",
    "Halo Backface Texture",
    "Halo Backface Preview Alpha",
    "Halo Backface Principled",
    "Halo Backface Mix",
)
_VISIBILITY_NODE_NAMES = (
    "Halo Visibility Light Path",
    "Halo Visibility Transparent",
    "Halo Visibility Mix",
)


def _configure_shader_backface_culling(
    material,
    backface_texture_id: str | None = None,
    backface_image=None,
    pack_root: str | os.PathLike[str] | None = None,
):
    """Build deterministic two-sided Ring shading for EEVEE and Cycles.

    A Ring with an explicit inner texture is represented by one double-sided
    cylinder in Blender.  ``Geometry.Backfacing`` selects the inner texture on
    the back side, avoiding coincident geometry entirely.  A Light Path gate
    keeps the full-bright preview visible to camera rays without turning Halo
    sprites into Cycles light sources or shadow casters.
    """

    if not material.use_nodes or material.node_tree is None:
        return False
    nodes = material.node_tree.nodes
    links = material.node_tree.links
    output = next(
        (node for node in nodes if node.bl_idname == "ShaderNodeOutputMaterial" and node.is_active_output),
        next((node for node in nodes if node.bl_idname == "ShaderNodeOutputMaterial"), None),
    )
    shader = next((node for node in nodes if node.bl_idname == "ShaderNodeBsdfPrincipled"), None)
    if output is None or shader is None:
        return False

    stored_backface_id = str(backface_texture_id or material.get("halo_backface_texture_id") or "")
    if backface_image is None:
        previous_texture = nodes.get("Halo Backface Texture")
        backface_image = getattr(previous_texture, "image", None)
    if backface_image is None and stored_backface_id:
        import bpy

        backface_image = next(
            (image for image in bpy.data.images if image.get("halo_texture_id") == stored_backface_id),
            None,
        )
        if backface_image is None:
            backface_image = load_texture_image(stored_backface_id, pack_root)

    for node_name in (*_BACKFACE_NODE_NAMES, *_VISIBILITY_NODE_NAMES):
        existing = nodes.get(node_name)
        if existing is not None:
            nodes.remove(existing)
    surface = output.inputs.get("Surface")
    if surface is None:
        return False
    for link in list(surface.links):
        links.remove(link)

    paired_texture = bool(stored_backface_id and backface_image is not None)
    visible_shader = shader.outputs["BSDF"]
    if paired_texture or material.use_backface_culling:
        geometry = nodes.new("ShaderNodeNewGeometry")
        geometry.name = "Halo Backface Geometry"
        geometry.label = "内外面检测"
        geometry.location = (120, -260)
        mix = nodes.new("ShaderNodeMixShader")
        mix.name = "Halo Backface Mix"
        mix.label = "Halo 内外面着色"
        mix.location = (390, 20)
        links.new(geometry.outputs["Backfacing"], mix.inputs[0])
        links.new(shader.outputs["BSDF"], mix.inputs[1])
        visible_shader = mix.outputs["Shader"]

    if paired_texture:
        texture = nodes.new("ShaderNodeTexImage")
        texture.name = "Halo Backface Texture"
        texture.label = "Cycles 对侧纹理"
        texture.location = (-260, -410)
        texture.image = backface_image
        texture.interpolation = "Linear"
        alpha_multiply = nodes.new("ShaderNodeMath")
        alpha_multiply.name = "Halo Backface Preview Alpha"
        alpha_multiply.label = "对侧 PNG Alpha × Preview Alpha"
        alpha_multiply.operation = "MULTIPLY"
        alpha_multiply.location = (-20, -500)
        alpha_multiply.inputs[1].default_value = float(material.get("halo_base_alpha", 1.0))
        if texture.outputs.get("Alpha") is not None:
            links.new(texture.outputs["Alpha"], alpha_multiply.inputs[0])
        back_shader = nodes.new("ShaderNodeBsdfPrincipled")
        back_shader.name = "Halo Backface Principled"
        back_shader.label = "Cycles 对侧着色"
        back_shader.location = (120, -180)
        links.new(texture.outputs["Color"], back_shader.inputs["Base Color"])
        if back_shader.inputs.get("Alpha") is not None:
            links.new(alpha_multiply.outputs[0], back_shader.inputs["Alpha"])
        emission = back_shader.inputs.get("Emission Color") or back_shader.inputs.get("Emission")
        if emission is not None:
            links.new(texture.outputs["Color"], emission)
        _set_socket(back_shader, ("Roughness",), 0.35)
        _set_socket(back_shader, ("Emission Strength",), 1.0 if material.get("halo_glowing", True) else 0.15)
        links.new(back_shader.outputs["BSDF"], mix.inputs[2])
        material["halo_backface_texture_id"] = stored_backface_id
        material["halo_shader_backface_mode"] = "PAIRED_TEXTURE"
    elif material.use_backface_culling:
        transparent = nodes.new("ShaderNodeBsdfTransparent")
        transparent.name = "Halo Backface Transparent"
        transparent.label = "背面透明"
        transparent.location = (350, -180)
        links.new(transparent.outputs["BSDF"], mix.inputs[2])
        material["halo_shader_backface_mode"] = "TRANSPARENT"
    else:
        material["halo_shader_backface_mode"] = "DOUBLE_SIDED"

    # Minecraft's glowing/full-bright appearance is visible coloration, not an
    # emissive area light.  Hide the shader from non-camera Cycles paths so the
    # preview does not cast colored indirect light, reflections, or shadows.
    light_path = nodes.new("ShaderNodeLightPath")
    light_path.name = "Halo Visibility Light Path"
    light_path.label = "仅摄像机可见"
    light_path.location = (390, -260)
    ray_transparent = nodes.new("ShaderNodeBsdfTransparent")
    ray_transparent.name = "Halo Visibility Transparent"
    ray_transparent.label = "非摄像机射线透明"
    ray_transparent.location = (610, -180)
    visibility_mix = nodes.new("ShaderNodeMixShader")
    visibility_mix.name = "Halo Visibility Mix"
    visibility_mix.label = "阻止间接发光"
    visibility_mix.location = (650, 20)
    output.location = (900, 20)
    links.new(light_path.outputs["Is Camera Ray"], visibility_mix.inputs[0])
    links.new(ray_transparent.outputs["BSDF"], visibility_mix.inputs[1])
    links.new(visible_shader, visibility_mix.inputs[2])
    links.new(visibility_mix.outputs["Shader"], surface)
    material["halo_shader_backface_culling"] = bool(material.use_backface_culling)
    material["halo_shader_side_selection"] = paired_texture
    material["halo_camera_only_shader"] = True
    return True


def refresh_halo_material_settings():
    """Upgrade materials stored by older extension versions in-place."""

    import bpy

    refreshed = 0
    for material in bpy.data.materials:
        if not material.get("halo_texture_id"):
            continue
        _set_blend_mode(material)
        _configure_shader_backface_culling(material)
        refreshed += 1

    # Collapse 0.1.16 and older two-surface Ring previews to one double-sided
    # surface.  JSON still retains ``inner_texture``; only Blender's preview
    # representation changes.
    for obj in bpy.data.objects:
        if obj.get("halo_role") != "primitive":
            continue
        node = getattr(obj, "halo_node", None)
        if node is None or node.primitive_type != "ring" or not node.inner_texture:
            continue
        mesh = getattr(obj, "data", None)
        if mesh is not None and len(mesh.polygons) == int(node.segments) and len(mesh.materials) == 1:
            continue
        from .geometry import ring_mesh

        old_mesh = obj.data
        obj.data = ring_mesh(obj.name, node.size, node.segments, False)
        scene = getattr(bpy.context, "scene", None)
        try:
            from .blender_scene import definition_pack_root
            pack_root = definition_pack_root(scene, obj.get("halo_definition_id", "")) if scene is not None else ""
        except Exception:
            project = getattr(scene, "halo_project", None)
            pack_root = getattr(project, "pack_root", "") if project is not None else ""
        group_node = getattr(getattr(obj, "parent", None), "halo_node", None)
        glowing = bool(group_node.glowing) if group_node is not None else True
        assign_primitive_materials(obj, node.texture, node.inner_texture, pack_root, glowing=glowing)
        if old_mesh and old_mesh.users == 0:
            bpy.data.meshes.remove(old_mesh)
    return refreshed


def _new_placeholder_image(texture_id: str):
    import bpy

    image = bpy.data.images.new(
        name="Missing Halo Texture - " + (texture_id or "unknown"),
        width=2,
        height=2,
        alpha=True,
    )
    # Magenta/black checkerboard makes missing files obvious in viewport.
    image.pixels = [
        1.0, 0.0, 1.0, 1.0,
        0.03, 0.0, 0.03, 1.0,
        0.03, 0.0, 0.03, 1.0,
        1.0, 0.0, 1.0, 1.0,
    ]
    image.pack()
    image["halo_missing_texture"] = True
    return image


def pack_halo_image(image):
    """Keep Halo PNGs portable without packing unrelated scene resources."""
    if image is not None and not image.get("halo_missing_texture"):
        try:
            if image.is_dirty or not image.packed_file:
                image.pack()
            image.pop("halo_pack_error", None)
        except RuntimeError as exc:
            image["halo_pack_error"] = str(exc)
    return image


def load_texture_image(texture_id: str, pack_root: str | os.PathLike[str] | None = None):
    """Load a pack texture or return a visible placeholder image."""

    import bpy

    # Generated Mesh-conversion textures may be packed in the .blend instead
    # of being written into an imported source pack.  Source-backed images,
    # however, must be matched by resolved path: two merged packs may legally
    # use the same resource ID for different PNG files.
    for image in bpy.data.images:
        if (
            image.get("halo_texture_id") == texture_id
            and image.get("halo_generated_texture")
            and not image.get("halo_missing_texture")
        ):
            return image
    path = resolve_texture_path(texture_id, pack_root)
    if path:
        for image in bpy.data.images:
            if (not image.get("halo_mask_image")
                    and os.path.normcase(os.path.abspath(bpy.path.abspath(getattr(image, "filepath", "")))) == os.path.normcase(path)):
                image["halo_texture_id"] = texture_id
                image["halo_source_path"] = path
                return pack_halo_image(image)
        try:
            image = bpy.data.images.load(path, check_existing=True)
            image.name = Path(path).name
            image["halo_texture_id"] = texture_id
            image["halo_source_path"] = path
            return pack_halo_image(image)
        except RuntimeError:
            pass
    # A packed image remains valid when its cache was deleted mid-session.
    # Match its original source path, not just its resource ID (merged packs).
    if pack_root:
        namespace, relative = split_resource_id(texture_id)
        candidate = Path(pack_root) / "assets" / namespace / relative
        candidates = {os.path.normcase(str(candidate.resolve()))}
        if not candidate.suffix:
            candidates.add(os.path.normcase(str(candidate.with_suffix(".png").resolve())))
        for image in bpy.data.images:
            source_path = image.get("halo_source_path", "")
            if (source_path and image.packed_file and not image.get("halo_missing_texture")
                    and os.path.normcase(str(Path(source_path).resolve())) in candidates):
                return image
    # Reuse placeholders by identifier to avoid making thousands of images
    # in a large pack with one missing texture.
    name = "Missing Halo Texture - " + (texture_id or "unknown")
    existing = bpy.data.images.get(name)
    return existing or _new_placeholder_image(texture_id)


def load_mask_image(texture_id: str, pack_root: str | os.PathLike[str] | None = None):
    """Load a separate non-color image datablock for a mesh alpha mask."""

    import bpy

    path = resolve_texture_path(texture_id, pack_root)
    for image in bpy.data.images:
        if not image.get("halo_mask_image") or image.get("halo_texture_id") != texture_id:
            continue
        source = image.get("halo_source_path") or image.filepath
        if path is None or (source and os.path.normcase(os.path.abspath(bpy.path.abspath(source))) == os.path.normcase(path)):
            return pack_halo_image(image)
    base = load_texture_image(texture_id, pack_root)
    image = base.copy()
    image.name = "Halo Mask - " + (Path(path).name if path else texture_id)
    image["halo_texture_id"] = texture_id
    image["halo_mask_image"] = True
    if path:
        image["halo_source_path"] = path
        image.filepath = path
    try:
        image.colorspace_settings.name = "Non-Color"
    except TypeError:
        pass
    return pack_halo_image(image)


def create_halo_material(
    texture_id: str,
    pack_root: str | os.PathLike[str] | None = None,
    *,
    glowing: bool = True,
    alpha: float = 1.0,
    backface_culling: bool = False,
    backface_texture_id: str | None = None,
    name: str | None = None,
):
    """Create or update a transparent Principled/Emission material."""

    import bpy

    material_name = name or "Halo " + (texture_id or "Texture")
    # Include glow in the key so toggling one group does not mutate another
    # group's shader unexpectedly.
    paired = f";back={backface_texture_id}" if backface_texture_id else ""
    source_token = hashlib.sha1(os.path.normcase(os.path.abspath(str(pack_root or ""))).encode("utf-8")).hexdigest()[:10]
    key = f"{material_name} [{'glow' if glowing else 'flat'};{'culled' if backface_culling else 'double'}{paired};src={source_token}]"
    material = bpy.data.materials.get(key) or bpy.data.materials.new(key)
    material.use_nodes = True
    _set_blend_mode(material)
    material["halo_texture_id"] = texture_id
    material["halo_glowing"] = bool(glowing)
    material["halo_base_alpha"] = float(alpha)
    material.use_backface_culling = bool(backface_culling)

    nodes = material.node_tree.nodes
    links = material.node_tree.links
    nodes.clear()
    output = nodes.new("ShaderNodeOutputMaterial")
    output.location = (420, 20)
    shader = nodes.new("ShaderNodeBsdfPrincipled")
    shader.location = (80, 20)
    texture = nodes.new("ShaderNodeTexImage")
    texture.location = (-260, 20)
    texture.image = load_texture_image(texture_id, pack_root)
    texture.interpolation = "Linear"
    links.new(texture.outputs.get("Color"), shader.inputs.get("Base Color"))
    alpha_multiply = nodes.new("ShaderNodeMath")
    alpha_multiply.name = "Halo Preview Alpha"
    alpha_multiply.label = "PNG Alpha × Preview Alpha"
    alpha_multiply.operation = "MULTIPLY"
    alpha_multiply.location = (-70, -180)
    alpha_multiply.inputs[1].default_value = float(alpha)
    if texture.outputs.get("Alpha") is not None:
        links.new(texture.outputs["Alpha"], alpha_multiply.inputs[0])
    if shader.inputs.get("Alpha") is not None:
        links.new(alpha_multiply.outputs[0], shader.inputs["Alpha"])
    # Blender changed the Principled socket names over time; set whichever is
    # available, and let the texture alpha connection above remain intact.
    _set_socket(shader, ("Roughness",), 0.35)
    _set_socket(shader, ("Emission Color", "Emission"), (1.0, 1.0, 1.0, 1.0))
    emission_socket = shader.inputs.get("Emission Color") or shader.inputs.get("Emission")
    if emission_socket is not None:
        links.new(texture.outputs.get("Color"), emission_socket)
    _set_socket(shader, ("Emission Strength",), 1.0 if glowing else 0.15)
    _set_socket(shader, ("Alpha",), float(alpha))
    paired_image = load_texture_image(backface_texture_id, pack_root) if backface_texture_id else None
    _configure_shader_backface_culling(material, backface_texture_id, paired_image, pack_root)

    missing = bool(getattr(texture.image, "get", lambda *_: False)("halo_missing_texture", False))
    material["halo_missing_texture"] = missing
    material.diffuse_color = (1.0, 0.0, 1.0, float(alpha)) if missing else (1.0, 1.0, 1.0, float(alpha))
    return material


def assign_material(obj, material, slot: int = 0):
    """Assign a material slot without duplicate entries."""

    if obj is None or getattr(obj, "data", None) is None:
        return
    slots = obj.data.materials
    while len(slots) <= slot:
        slots.append(None)
    slots[slot] = material


def assign_primitive_materials(
    obj,
    texture_id: str,
    inner_texture_id: str | None = None,
    pack_root: str | os.PathLike[str] | None = None,
    *,
    glowing: bool = True,
):
    """Assign outer and optional inner ring materials, or a billboard slot."""

    outer = create_halo_material(
        texture_id,
        pack_root,
        glowing=glowing,
        backface_culling=False,
        backface_texture_id=inner_texture_id,
    )
    assign_material(obj, outer, 0)
    return outer


def assign_mesh_material(
    obj,
    texture_id: str,
    pack_root: str | os.PathLike[str] | None = None,
    *,
    glowing: bool = True,
    double_sided: bool = True,
    mask_texture_id: str = "",
    mask_mode: str = "linear",
    mask_threshold: float = 0.5,
):
    """Build Halo 2.0's base texture plus optional animated alpha-mask graph."""

    material = create_halo_material(
        texture_id,
        pack_root,
        glowing=glowing,
        backface_culling=not double_sided,
        # Mask UV offsets are animated per primitive.  A dedicated material
        # datablock prevents two meshes that share PNGs from overwriting each
        # other's shader uniforms on the same frame.
        name=f"Halo Mesh {texture_id or 'Texture'} [{obj.get('halo_uuid', obj.name)}]",
    )
    material["halo_mesh_material"] = True
    material["halo_mesh_double_sided"] = bool(double_sided)
    material["halo_mesh_mask_texture_id"] = str(mask_texture_id or "")
    material["halo_mesh_mask_mode"] = str(mask_mode or "linear")
    material["halo_mesh_mask_threshold"] = float(mask_threshold)
    if mask_texture_id and material.use_nodes:
        nodes = material.node_tree.nodes
        links = material.node_tree.links
        preview_alpha = nodes.get("Halo Preview Alpha")
        base_texture = next((node for node in nodes if node.bl_idname == "ShaderNodeTexImage" and node.name != "Halo Backface Texture"), None)
        if preview_alpha is not None and base_texture is not None:
            for link in list(preview_alpha.inputs[0].links):
                links.remove(link)
            coordinates = nodes.new("ShaderNodeTexCoord")
            coordinates.name = "Halo Mask Coordinates"
            coordinates.location = (-680, -350)
            offset = nodes.new("ShaderNodeVectorMath")
            offset.name = "Halo Mask UV Offset"
            offset.operation = "ADD"
            offset.location = (-500, -350)
            offset.inputs[1].default_value = (0.0, 0.0, 0.0)
            mask_texture = nodes.new("ShaderNodeTexImage")
            mask_texture.name = "Halo Mask Texture"
            mask_texture.location = (-300, -360)
            mask_texture.image = load_mask_image(mask_texture_id, pack_root)
            mask_texture.interpolation = "Closest"
            mask_texture.extension = "REPEAT"
            separate = nodes.new("ShaderNodeSeparateColor")
            separate.name = "Halo Mask Red"
            separate.mode = "RGB"
            separate.location = (-90, -380)
            mask_factor = separate.outputs.get("Red")
            if str(mask_mode).lower() == "step":
                below = nodes.new("ShaderNodeMath")
                below.name = "Halo Mask Below Threshold"
                below.operation = "LESS_THAN"
                below.location = (20, -410)
                below.inputs[1].default_value = float(mask_threshold)
                links.new(separate.outputs["Red"], below.inputs[0])
                step = nodes.new("ShaderNodeMath")
                step.name = "Halo Mask Step"
                step.operation = "SUBTRACT"
                step.location = (110, -350)
                step.inputs[0].default_value = 1.0
                links.new(below.outputs[0], step.inputs[1])
                mask_factor = step.outputs[0]
            combine = nodes.new("ShaderNodeMath")
            combine.name = "Halo Mesh Base × Mask Alpha"
            combine.operation = "MULTIPLY"
            combine.location = (80, -220)
            links.new(coordinates.outputs["UV"], offset.inputs[0])
            links.new(offset.outputs["Vector"], mask_texture.inputs["Vector"])
            links.new(mask_texture.outputs["Color"], separate.inputs["Color"])
            links.new(base_texture.outputs["Alpha"], combine.inputs[0])
            links.new(mask_factor, combine.inputs[1])
            links.new(combine.outputs[0], preview_alpha.inputs[0])
            if mask_texture.image and mask_texture.image.get("halo_missing_texture"):
                material["halo_missing_texture"] = True
    assign_material(obj, material, 0)
    return material


def mesh_mask_resolution_warning(material) -> str | None:
    """Describe an incompatible loaded base/mask PNG pair, if present."""

    if material is None or not getattr(material, "use_nodes", False):
        return None
    nodes = material.node_tree.nodes
    mask_node = nodes.get("Halo Mask Texture")
    base_node = next((node for node in nodes if node.bl_idname == "ShaderNodeTexImage"
                      and node.name not in {"Halo Mask Texture", "Halo Backface Texture"}), None)
    if mask_node is None or mask_node.image is None or base_node is None or base_node.image is None:
        return None
    if mask_node.image.get("halo_missing_texture") or base_node.image.get("halo_missing_texture"):
        return None
    base_width, base_height = (int(value) for value in base_node.image.size[:2])
    mask_width, mask_height = (int(value) for value in mask_node.image.size[:2])
    if has_integral_texture_scale(base_width, base_height, mask_width, mask_height):
        return None
    return (
        f"Alpha Mask 为 {mask_width}×{mask_height}，主纹理为 {base_width}×{base_height}；"
        f"请调整 PNG 像素尺寸，使宽高使用同一整数倍（例如 {base_width}×{base_height}）"
    )


def set_mesh_mask_offset(material, offset_u: float, offset_v: float):
    if material is None or not material.use_nodes:
        return
    node = material.node_tree.nodes.get("Halo Mask UV Offset")
    if node is not None:
        node.inputs[1].default_value = (float(offset_u) % 1.0, float(offset_v) % 1.0, 0.0)


def set_material_visual(material, alpha: float = 1.0, glow: float = 1.0):
    """Apply frame-preview alpha/glow values to an existing material."""

    if material is None:
        return
    material["halo_preview_alpha"] = float(alpha)
    material["halo_preview_glow"] = float(glow)
    material.diffuse_color = (*material.diffuse_color[:3], max(0.0, min(1.0, float(alpha))))
    if material.use_nodes:
        for node in material.node_tree.nodes:
            if node.bl_idname == "ShaderNodeBsdfPrincipled":
                _set_socket(node, ("Emission Strength",), max(0.0, float(glow)))
            elif node.name in {"Halo Preview Alpha", "Halo Backface Preview Alpha"} and node.bl_idname == "ShaderNodeMath":
                node.inputs[1].default_value = max(0.0, min(1.0, float(alpha)))


def texture_destination(pack_root: str | os.PathLike[str], texture_id: str) -> str:
    """Return a safe destination path for an imported texture."""

    namespace, relative = split_resource_id(texture_id)
    root = Path(pack_root).resolve()
    destination = (root / "assets" / namespace / relative).resolve()
    destination.relative_to(root)  # raises if a caller supplied traversal
    destination.parent.mkdir(parents=True, exist_ok=True)
    return str(destination)


def _texture_family(path: Path) -> dict[str, Path]:
    """A material includes its PNG, labPBR companions and animation metadata."""
    result = {}
    for suffix in ("", "_n", "_s", "_e"):
        image = path.with_name(path.stem + suffix + path.suffix)
        for key, candidate in ((suffix, image), (suffix + ".mcmeta", Path(str(image) + ".mcmeta"))):
            if candidate.is_file():
                result[key] = candidate
    return result


def _texture_signature(family: dict[str, Path]):
    signature = {}
    for key, path in family.items():
        with path.open("rb") as stream:
            signature[key] = hashlib.file_digest(stream, "sha256").digest()
    return signature


def copy_texture_with_sidecars(
    source: str, pack_root: str, texture_id: str, *, reuse_candidates: Iterable[str] = (),
    allow_missing_base: bool = False,
    other_pack_roots: Iterable[str] = (),
) -> list[str]:
    """Reuse identical material content; suffix only genuine name conflicts.

    Optional candidates are already bound images in this source/namespace.
    Byte signatures include sidecars so two different material families never
    overwrite each other just because their color PNGs happen to be identical.
    """

    texture_id = lowercase_resource_identifier(texture_id)
    source_path = Path(source).resolve()
    if not source_path.is_file() and not allow_missing_base:
        raise FileNotFoundError(str(source_path))
    destination = Path(texture_destination(pack_root, texture_id))
    namespace_root = Path(pack_root).resolve() / "assets" / split_resource_id(texture_id)[0]
    family = _texture_family(source_path)
    signature = _texture_signature(family)
    def conflicts_with_other_source(candidate):
        relative = candidate.relative_to(Path(pack_root).resolve())
        for other in other_pack_roots:
            target = Path(other).resolve() / relative
            existing = _texture_family(target)
            if (existing and _texture_signature(existing) != signature) or target.is_dir():
                return True
        return False

    # Old imports may already have _1/_2 copies. Reuse one instead of growing
    # a new suffix on each import, including when the unsuffixed file differs.
    numbered = sorted(p for p in destination.parent.glob(destination.stem + "_*" + destination.suffix)
                      if p.stem[len(destination.stem) + 1:].isdigit())
    seen = set()
    for candidate in (destination, *map(Path, reuse_candidates), *numbered):
        candidate = candidate.resolve()
        if candidate in seen or not candidate.is_relative_to(namespace_root.resolve()):
            continue
        seen.add(candidate)
        existing_family = _texture_family(candidate)
        if (existing_family and _texture_signature(existing_family) == signature
                and not conflicts_with_other_source(candidate)):
            return [str(candidate), *(str(existing_family[key]) for key in family if key)]
    selected = destination
    index = 1
    while _texture_family(selected) or selected.exists() or conflicts_with_other_source(selected):
        selected = destination.with_name(f"{destination.stem}_{index}{destination.suffix}")
        index += 1
    # Namespace renaming also supports incomplete old packs: the first path
    # remains the base image's destination even when only sidecars survived.
    copied = [str(selected)]
    for key, candidate in family.items():
        suffix = key.removesuffix(".mcmeta")
        target = selected.with_name(selected.stem + suffix + selected.suffix)
        if key.endswith(".mcmeta"):
            target = Path(str(target) + ".mcmeta")
        shutil.copy2(candidate, target)
        if key:
            copied.append(str(target))
    return copied


__all__ = [
    "split_resource_id",
    "resolve_texture_path",
    "load_texture_image",
    "load_mask_image",
    "create_halo_material",
    "assign_material",
    "assign_primitive_materials",
    "assign_mesh_material",
    "mesh_mask_resolution_warning",
    "set_material_visual",
    "set_mesh_mask_offset",
    "refresh_halo_material_settings",
    "texture_destination",
    "copy_texture_with_sidecars",
]
