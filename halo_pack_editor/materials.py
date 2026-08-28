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
            if os.path.normcase(os.path.abspath(getattr(image, "filepath", ""))) == os.path.normcase(path):
                return image
        try:
            image = bpy.data.images.load(path, check_existing=True)
            image.name = Path(path).name
            image["halo_texture_id"] = texture_id
            image["halo_source_path"] = path
            return image
        except RuntimeError:
            pass
    # Reuse placeholders by identifier to avoid making thousands of images
    # in a large pack with one missing texture.
    name = "Missing Halo Texture - " + (texture_id or "unknown")
    existing = bpy.data.images.get(name)
    return existing or _new_placeholder_image(texture_id)


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


def copy_texture_with_sidecars(source: str, pack_root: str, texture_id: str) -> list[str]:
    """Copy a PNG and any labPBR sidecars, returning destination paths."""

    source_path = Path(source).resolve()
    destination = Path(texture_destination(pack_root, texture_id))
    selected = destination
    if selected.exists() and selected.resolve() != source_path:
        index = 1
        while selected.exists():
            selected = destination.with_name(f"{destination.stem}_{index}{destination.suffix}")
            index += 1
    copied = []
    candidates = (("", source_path), *[(suffix, source_path.with_name(source_path.stem + suffix + source_path.suffix)) for suffix in ("_n", "_s", "_e")])
    for sidecar_suffix, candidate in candidates:
        if candidate.is_file():
            target = selected.with_name(selected.stem + sidecar_suffix + selected.suffix)
            if target.resolve() != candidate.resolve():
                shutil.copy2(candidate, target)
            copied.append(str(target))
    return copied


__all__ = [
    "split_resource_id",
    "resolve_texture_path",
    "load_texture_image",
    "create_halo_material",
    "assign_material",
    "assign_primitive_materials",
    "set_material_visual",
    "refresh_halo_material_settings",
    "texture_destination",
    "copy_texture_with_sidecars",
]
