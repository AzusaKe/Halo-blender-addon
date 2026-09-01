"""Relocate definition textures without changing geometry or shared images."""

import json
from pathlib import Path

from .core.texture_migration import migrate_texture_families, remap_texture_references
from .core.texture_usage import referenced_texture_ids, texture_candidates


def migrate_definition_textures(scene, item, old_id, namespace):
    """Called under the ID guard with the old ID still visible to scene APIs."""
    import bpy
    from .blender_scene import definition_pack_root, sync_definition_from_scene
    from .materials import pack_halo_image
    from .resource_store import ensure_resources

    ensure_resources(scene, rebind=False)
    source = next((source for source in scene.halo_project.sources if source.source_id == item.source_id), None)
    if source is not None and source.get("halo_resource_error"):
        raise ValueError(source["halo_resource_error"])
    raw = sync_definition_from_scene(scene, old_id)
    if raw is None:
        return [], []
    identifiers = referenced_texture_ids([raw])
    pack_root = Path(definition_pack_root(scene, old_id)).resolve()

    # Mesh bakes may exist only in packed Images. Copy their encoded pixels
    # to the working source without renaming the original Image (it may be
    # shared). Reserve existing generated targets too, so suffix selection
    # cannot steal another definition's generated texture ID.
    for image in bpy.data.images:
        identifier = image.get("halo_texture_id", "")
        candidates = texture_candidates(identifier)
        if (not image.get("halo_generated_texture") or image.get("halo_missing_texture")
                or not candidates or (identifier not in identifiers
                and not candidates[-1].startswith(f"assets/{namespace}/"))):
            continue
        target = (pack_root / candidates[-1]).resolve()
        target.relative_to(pack_root)
        pack_halo_image(image)
        if not image.packed_file:
            raise ValueError(f"无法读取生成贴图：{identifier}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(bytes(image.packed_file.data))

    # Finish all filesystem work before changing any resource references.
    other_roots = [entry.pack_root for entry in scene.halo_project.sources
                   if entry.pack_root and Path(entry.pack_root).resolve() != pack_root]
    mapping, notices = migrate_texture_families(pack_root, identifiers, namespace, other_pack_roots=other_roots)
    objects = [obj for obj in scene.objects if obj.get("halo_definition_id") == old_id]

    def rewrite(text):
        try:
            return json.dumps(remap_texture_references(json.loads(text), mapping), ensure_ascii=False, indent=2)
        except (ValueError, TypeError):
            return text  # Leave unrelated malformed advanced-editor text alone.

    for field in ("raw_json", "animation_json", "startup_json", "shutdown_json"):
        setattr(item, field, rewrite(getattr(item, field)))
    for obj in objects:
        for key in ("halo_raw_json", "halo_primitive_raw_json"):
            if key in obj:
                obj[key] = rewrite(obj[key])
        node = getattr(obj, "halo_node", None)
        if node is None:
            continue
        obj["halo_property_update_guard"] = True
        try:
            node.raw_json = rewrite(node.raw_json)
            node.animation_json = rewrite(node.animation_json)
            if obj.get("halo_role") == "primitive":
                node.texture = mapping.get(node.texture, node.texture)
                node.inner_texture = mapping.get(node.inner_texture, node.inner_texture)
                obj["halo_texture_id"] = node.texture
                obj["halo_inner_texture_id"] = node.inner_texture
        finally:
            obj.pop("halo_property_update_guard", None)
    return [obj for obj in objects if obj.get("halo_role") == "primitive"], notices


def refresh_migrated_materials(scene, primitives):
    from .blender_scene import definition_pack_root
    from .materials import assign_primitive_materials
    from .handlers import update_animation

    for obj in primitives:
        node = obj.halo_node
        group = getattr(obj.parent, "halo_node", None)
        assign_primitive_materials(obj, node.texture, node.inner_texture or None,
                                   definition_pack_root(scene, obj.get("halo_definition_id", "")),
                                   glowing=bool(group.glowing) if group else True)
    update_animation(scene)
