"""Blender 5.2 background integration test for Halo Pack Editor."""

from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path

import bpy
from mathutils import Quaternion, Vector


project_root = Path(sys.argv[sys.argv.index("--") + 1]).resolve()
cache_root = Path(r"F:\codex-cache\halo-blender-addon\tests")
cache_root.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(project_root))

import halo_pack_editor
from halo_pack_editor import blender_scene, handlers, operators, panels
from halo_pack_editor.core.pack_io import import_pack
from halo_pack_editor.geometry import mc_rotation_quaternion, mc_to_blender, ring_mesh
from halo_pack_editor.materials import assign_material, create_halo_material, resolve_texture_path
from halo_pack_editor.mesh_conversion import (
    DirectUVUnsupported,
    _coplanar_clusters,
    _direct_uv_material,
    _expand_polygon_border,
    iter_mesh_conversion,
)


halo_pack_editor.register()
scene = bpy.context.scene
assert bpy.ops.halo.convert_mesh.get_rna_type().properties["direct_uv_sampling"].default is False
source = Path(r"F:\HaloPackTool\output\Individual\Hina.zip")
data = blender_scene.import_project_to_scene(bpy.context, source, replace=True)
assert len(data["definitions"]) == 1

objects = [obj for obj in bpy.data.objects if obj.get("halo_definition_id")]
groups = [obj for obj in objects if obj.get("halo_role") == "group"]
primitives = [obj for obj in objects if obj.get("halo_role") == "primitive"]
assert len(groups) == 57, len(groups)
assert len(primitives) == 29, len(primitives)
assert panels._definition_primitive_count(scene, scene.halo_project.active_definition) == 29

# Convert one ordinary triangular Mesh into a wrapper group containing a flat
# face group and a baked Billboard.  The transparent half of the minimum
# covering rectangle must survive the PNG bake, and cleanup restores the Hina
# tree before the remaining regression tests run.
source_mesh = bpy.data.meshes.new("Mesh Conversion Triangle")
source_mesh.from_pydata(
    [
        (0.0, 0.0, 0.0), (2.0, 0.0, 0.0), (0.0, 1.0, 0.0), (2.0, 1.0, 0.0),
        (3.0, 0.0, 0.0), (3.0, 1.0, 0.0), (3.0, 1.0, 2.0), (3.0, 0.0, 2.0),
    ],
    [],
    [(0, 1, 2), (1, 3, 2), (4, 5, 6, 7)],
)
source_mesh.update()
source_uv = source_mesh.uv_layers.new(name="UVMap")
for loop, uv in zip(source_uv.data, (
    (0.55, 0.55), (0.95, 0.55), (0.55, 0.95),
    (0.95, 0.55), (0.95, 0.95), (0.55, 0.95),
    (0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0),
)):
    loop.uv = uv
source_object = bpy.data.objects.new("Triangle Source Mesh", source_mesh)
scene.collection.objects.link(source_object)
source_object.location = (11.0, -4.0, 7.0)
source_object.rotation_euler = (0.3, 0.5, -0.2)
source_object.scale = (2.0, 3.0, 4.0)
source_material = bpy.data.materials.new("Triangle Source Material")
source_material.use_nodes = True
source_shader = next(node for node in source_material.node_tree.nodes if node.bl_idname == "ShaderNodeBsdfPrincipled")
source_atlas = bpy.data.images.new("Mesh Conversion Source Atlas", width=16, height=16, alpha=True)
source_atlas_pixels = []
for atlas_y in range(16):
    for atlas_x in range(16):
        # Only the upper-right atlas quadrant used by the triangle is green.
        # If bake output UVs leak into material sampling, red pixels from the
        # other three quadrants will appear in the converted face texture.
        source_atlas_pixels.extend(
            (0.05, 0.9, 0.1, 1.0) if atlas_x >= 8 and atlas_y >= 8 else (0.9, 0.05, 0.1, 1.0)
        )
source_atlas.pixels = source_atlas_pixels
source_nodes = source_material.node_tree.nodes
source_links = source_material.node_tree.links
source_texcoord = source_nodes.new("ShaderNodeTexCoord")
source_image_node = source_nodes.new("ShaderNodeTexImage")
source_image_node.image = source_atlas
source_image_node.interpolation = "Closest"
source_links.new(source_texcoord.outputs["UV"], source_image_node.inputs["Vector"])
source_links.new(source_image_node.outputs["Color"], source_shader.inputs["Base Color"])
source_mesh.materials.append(source_material)
source_material_blue = bpy.data.materials.new("Vertical Source Material")
source_material_blue.use_nodes = True
source_shader_blue = next(node for node in source_material_blue.node_tree.nodes if node.bl_idname == "ShaderNodeBsdfPrincipled")
source_shader_blue.inputs["Base Color"].default_value = (0.1, 0.2, 0.9, 1.0)
source_mesh.materials.append(source_material_blue)
source_mesh.polygons[2].material_index = 1
# The conservative fast path must reject coordinate-processing node chains;
# the public converter then falls back to the existing Cycles bake for these
# and all other complex materials.
source_links.remove(source_image_node.inputs["Vector"].links[0])
source_mapping = source_nodes.new("ShaderNodeMapping")
source_links.new(source_texcoord.outputs["UV"], source_mapping.inputs["Vector"])
source_links.new(source_mapping.outputs["Vector"], source_image_node.inputs["Vector"])
try:
    _direct_uv_material(source_object, [source_mesh.polygons[0]], source_mesh)
except DirectUVUnsupported:
    pass
else:
    raise AssertionError("Mapping node must force the direct UV path to fall back")
assert sorted(len(cluster) for cluster in _coplanar_clusters(source_mesh, source_mesh.polygons, True)) == [1, 2]
assert len(_coplanar_clusters(source_mesh, source_mesh.polygons, False)) == 3
for selected in bpy.context.selected_objects:
    selected.select_set(False)
conversion_parent = groups[0]
conversion_parent.select_set(True)
bpy.context.view_layer.objects.active = conversion_parent
# The UI consumes the same iterator one cluster per timer event.  Closing it
# after the first baked cluster must clean all packed images/temp data without
# ever creating a partial Halo wrapper tree.
generated_before_cancel = {image.name for image in bpy.data.images if image.get("halo_generated_texture")}
children_before_cancel = set(conversion_parent.children)
cancel_iterator = iter_mesh_conversion(
    bpy.context,
    source_object,
    conversion_parent,
    texture_resolution=16,
    apply_modifiers=True,
    edge_padding=0,
    merge_coplanar=True,
    direct_uv_sampling=True,
)
prepared_update = next(cancel_iterator)
assert prepared_update["phase"] == "PREPARED" and prepared_update["total"] == 2
baked_update = next(cancel_iterator)
assert baked_update["phase"] == "BAKING" and baked_update["completed"] == 1
fallback_images = [
    image for image in bpy.data.images
    if image.name not in generated_before_cancel and image.get("halo_generated_texture")
]
assert len(fallback_images) == 1
assert not fallback_images[0].get("halo_direct_uv")
cancel_iterator.close()
assert {image.name for image in bpy.data.images if image.get("halo_generated_texture")} == generated_before_cancel
assert set(conversion_parent.children) == children_before_cancel
source_nodes.remove(source_mapping)
source_links.new(source_texcoord.outputs["UV"], source_image_node.inputs["Vector"])
assert bpy.ops.halo.convert_mesh(
    source_object=source_object.name,
    texture_resolution=32,
    apply_modifiers=True,
    edge_padding=2,
    merge_coplanar=True,
    direct_uv_sampling=True,
) == {"FINISHED"}
converted_wrapper = bpy.context.active_object
assert converted_wrapper.get("halo_role") == "group"
assert converted_wrapper.parent == conversion_parent
assert converted_wrapper.halo_node.node_id == source_object.name
assert tuple(round(value, 6) for value in converted_wrapper.halo_node.position) == (0.0, 0.0, 0.0)
converted_faces = [child for child in converted_wrapper.children if child.get("halo_role") == "group"]
assert len(converted_faces) == 2
converted_primitives = [child for face in converted_faces for child in face.children if child.get("halo_role") == "primitive"]
assert len(converted_primitives) == 2
assert panels._definition_primitive_count(scene, scene.halo_project.active_definition) == 31
horizontal_face = min(converted_faces, key=lambda face: abs(face.matrix_basis.translation.z))
vertical_face = max(converted_faces, key=lambda face: face.matrix_basis.translation.x)
converted_primitive = next(child for child in horizontal_face.children if child.get("halo_role") == "primitive")
assert converted_primitive.halo_node.primitive_type == "billboard"
assert sorted(round(value, 5) for value in converted_primitive.halo_node.size) == [1.0, 2.0]
converted_corners = {
    tuple(round(value, 5) for value in (horizontal_face.matrix_basis @ vertex.co))
    for vertex in converted_primitive.data.vertices
}
assert converted_corners == {
    (0.0, 0.0, 0.0),
    (2.0, 0.0, 0.0),
    (2.0, 1.0, 0.0),
    (0.0, 1.0, 0.0),
}, converted_corners
converted_normal = (horizontal_face.matrix_basis.to_3x3() @ converted_primitive.data.polygons[0].normal).normalized()
assert converted_normal.dot(Vector((0.0, 0.0, 1.0))) > 0.99999, converted_normal
vertical_primitive = next(child for child in vertical_face.children if child.get("halo_role") == "primitive")
vertical_normal = (vertical_face.matrix_basis.to_3x3() @ vertical_primitive.data.polygons[0].normal).normalized()
assert vertical_normal.dot(Vector((1.0, 0.0, 0.0))) > 0.99999, vertical_normal
converted_image = next(node.image for node in converted_primitive.data.materials[0].node_tree.nodes if node.bl_idname == "ShaderNodeTexImage")
assert converted_image.get("halo_texture_id") == converted_primitive.halo_node.texture
assert converted_image.get("halo_generated_texture") is True
assert converted_image.get("halo_direct_uv") is True
assert converted_image.packed_file is not None
# Conversion is copy-on-write: the imported source pack remains untouched.
assert resolve_texture_path(converted_primitive.halo_node.texture, scene.halo_project.pack_root) is None
alpha_values = list(converted_image.pixels)[3::4]
# The two coplanar source triangles form one rectangle.  Their shared diagonal
# must disappear completely in the one merged alpha image.
assert min(alpha_values) > 0.95
converted_pixels = list(converted_image.pixels)
opaque_red = 0.0
opaque_green = 0.0
for pixel_offset in range(0, len(converted_pixels), 4):
    if converted_pixels[pixel_offset + 3] > 0.9:
        opaque_red += converted_pixels[pixel_offset]
        opaque_green += converted_pixels[pixel_offset + 1]
assert opaque_green > opaque_red * 4.0, (opaque_red, opaque_green)
opaque_green_values = [
    converted_pixels[offset + 1]
    for offset in range(0, len(converted_pixels), 4)
    if converted_pixels[offset + 3] > 0.9
]
assert max(opaque_green_values) - min(opaque_green_values) < 1.0e-6

# Exact RGBA edge padding remains covered independently of the merged-quad
# test: two 8-neighbor rings around one seed produce a 5x5 block without any
# averaging or darkening.
padding_pixels = [0.0] * (7 * 7 * 4)
padding_mask = [0.0] * (7 * 7)
padding_seed = 3 * 7 + 3
padding_mask[padding_seed] = 1.0
padding_pixels[padding_seed * 4:padding_seed * 4 + 4] = [0.2, 0.7, 0.4, 1.0]
_expand_polygon_border(padding_pixels, padding_mask, 7, 7, 2)
padding_visible = [
    tuple(padding_pixels[offset:offset + 4])
    for offset in range(0, len(padding_pixels), 4)
    if padding_pixels[offset + 3] > 0.95
]
assert len(padding_visible) == 25
assert set(padding_visible) == {(0.2, 0.7, 0.4, 1.0)}
vertical_image = next(node.image for node in vertical_primitive.data.materials[0].node_tree.nodes if node.bl_idname == "ShaderNodeTexImage")
assert vertical_image.get("halo_direct_uv") is True
vertical_pixels = list(vertical_image.pixels)
center_offset = ((int(vertical_image.size[1]) // 2) * int(vertical_image.size[0]) + int(vertical_image.size[0]) // 2) * 4
assert vertical_pixels[center_offset + 2] > vertical_pixels[center_offset] * 2.0
conversion_zip = cache_root / "mesh-conversion-export.zip"
manifest_before_missing_description_test = scene.halo_project.manifest_json
manifest_without_description = json.loads(manifest_before_missing_description_test)
manifest_without_description.setdefault("pack", {}).pop("description", None)
manifest_without_description["unknown_meta_test"] = {"keep": True}
scene.halo_project.manifest_json = json.dumps(manifest_without_description, ensure_ascii=False, indent=2)
blender_scene.export_pack_from_scene(scene, conversion_zip, zip_output=True, overwrite=True)
namespace, relative_texture = converted_primitive.halo_node.texture.split(":", 1)
with zipfile.ZipFile(conversion_zip, "r") as archive:
    assert f"assets/{namespace}/{relative_texture}" in archive.namelist()
    repaired_manifest = json.loads(archive.read("pack.mcmeta"))
    assert repaired_manifest["pack"]["pack_format"] == 15
    assert repaired_manifest["pack"]["supported_formats"] == {"min_inclusive": 15, "max_inclusive": 88}
    assert repaired_manifest["pack"]["min_format"] == [15, 0]
    assert repaired_manifest["pack"]["max_format"] == [88, 0]
    assert repaired_manifest["pack"]["description"] == "Halo Pack Editor export"
    assert repaired_manifest["unknown_meta_test"] == {"keep": True}
scene.halo_project.manifest_json = manifest_before_missing_description_test
conversion_zip.unlink()
mesh_conversion_blend = cache_root / "mesh-conversion.blend"
bpy.ops.wm.save_as_mainfile(filepath=str(mesh_conversion_blend))
converted_objects = [converted_wrapper, *converted_faces, *converted_primitives]
converted_meshes = [obj.data for obj in converted_objects if getattr(obj, "data", None) is not None]
converted_materials = [material for mesh in converted_meshes for material in getattr(mesh, "materials", ()) if material]
blender_scene.remove_object_tree(converted_wrapper)
bpy.data.objects.remove(source_object, do_unlink=True)
for mesh in converted_meshes:
    if mesh.users == 0:
        bpy.data.meshes.remove(mesh)
for material in converted_materials:
    if material.users == 0:
        bpy.data.materials.remove(material)
for image in (converted_image, vertical_image):
    if image.users == 0:
        bpy.data.images.remove(image)
if source_mesh.users == 0:
    bpy.data.meshes.remove(source_mesh)
if source_material.users == 0:
    bpy.data.materials.remove(source_material)
if source_material_blue.users == 0:
    bpy.data.materials.remove(source_material_blue)
if source_atlas.users == 0:
    bpy.data.images.remove(source_atlas)
blender_scene.sync_definition_from_scene(scene, scene.halo_project.active_definition)
assert panels._definition_primitive_count(scene, scene.halo_project.active_definition) == 29
conversion_parent.select_set(True)
bpy.context.view_layer.objects.active = conversion_parent

# Renaming an active definition is atomic: the UI lookup key, stable root,
# complete object tree and both raw JSON copies change together.
old_definition_id = scene.halo_project.active_definition
definition_item = next(item for item in scene.halo_project.definitions if item.definition_id == old_definition_id)
renamed_definition_id = old_definition_id + "_editor_test"
definition_item.definition_id = renamed_definition_id
assert scene.halo_project.active_definition == renamed_definition_id
assert next((item for item in scene.halo_project.definitions if item.definition_id == scene.halo_project.active_definition), None) == definition_item
assert all(obj.get("halo_definition_id") == renamed_definition_id for obj in objects)
definition_root = next(obj for obj in objects if obj.get("halo_role") == "definition_root")
assert json.loads(definition_root.get("halo_raw_json", "{}"))["id"] == renamed_definition_id
assert json.loads(definition_item.raw_json)["id"] == renamed_definition_id

# Damping is editable at definition level, including exact zero values.  The
# panel update writes both lossless JSON copies immediately and must preserve
# future/unknown keys inside the damping object.  Physics preview is outside
# this test; this verifies import/edit/export data flow only.
item_raw = json.loads(definition_item.raw_json)
root_raw = json.loads(definition_root.get("halo_raw_json", "{}"))
item_raw.setdefault("damping", {})["futureDampingField"] = {"keep": True}
root_raw.setdefault("damping", {})["futureDampingField"] = {"keep": True}
definition_item.raw_json = json.dumps(item_raw, ensure_ascii=False, indent=2)
definition_root["halo_raw_json"] = json.dumps(root_raw, ensure_ascii=False, indent=2)
definition_item.damping_linear_factor = 0.0
definition_item.damping_angular_factor = 0.42
definition_item.damping_max_linear = 0.0
definition_item.damping_max_angular = 73.0
definition_item.allow_angular_momentum = True
definition_item.damping_angular_momentum_factor = 0.88
definition_item.damping_max_angular_momentum = 19.0
for damping_raw in (
    json.loads(definition_item.raw_json)["damping"],
    json.loads(definition_root.get("halo_raw_json", "{}"))["damping"],
):
    assert damping_raw["linearFactor"] == 0.0
    assert abs(damping_raw["angularFactor"] - 0.42) < 1e-6
    assert damping_raw["maxLinearDistance"] == 0.0
    assert damping_raw["maxAngularDegrees"] == 73.0
    assert abs(damping_raw["angularMomentumFactor"] - 0.88) < 1e-6
    assert damping_raw["maxAngularMomentumDegrees"] == 19.0
    assert damping_raw["futureDampingField"] == {"keep": True}
assert json.loads(definition_item.raw_json)["allow_angular_momentum"] is True

def group_depth(obj):
    depth = 0
    while obj.parent and obj.parent.get("halo_role") == "group":
        depth += 1
        obj = obj.parent
    return depth


def raw_contains_group_id(groups_raw, target_id):
    for group_raw in groups_raw if isinstance(groups_raw, list) else []:
        if not isinstance(group_raw, dict):
            continue
        if group_raw.get("id") == target_id or raw_contains_group_id(group_raw.get("children", []), target_id):
            return True
    return False

assert max(map(group_depth, groups)) >= 3

# Tree editing exposes readable legal parents instead of requiring users to
# copy UUIDs.  The active group and all descendants must be absent to prevent
# cycles, while the definition root remains available for restoring a group
# to the top level.
top_level_groups = [group for group in groups if group.parent == definition_root]
assert len(top_level_groups) >= 2
moving_group, target_group = top_level_groups[:2]
moving_position = tuple(moving_group.halo_node.position)
moving_rotation = tuple(moving_group.halo_node.rotation)
moving_scale = float(moving_group.halo_node.scale)
for selected in bpy.context.selected_objects:
    selected.select_set(False)
moving_group.select_set(True)
bpy.context.view_layer.objects.active = moving_group
parent_items = operators._reparent_target_items(None, bpy.context)
parent_ids = {item[0] for item in parent_items}
assert definition_root.get("halo_uuid") in parent_ids
assert target_group.get("halo_uuid") in parent_ids
assert moving_group.get("halo_uuid") not in parent_ids
for descendant in moving_group.children_recursive:
    if descendant.get("halo_role") == "group":
        assert descendant.get("halo_uuid") not in parent_ids
assert bpy.ops.halo.reparent(target_uuid=target_group.get("halo_uuid"), preserve_world=False) == {"FINISHED"}
assert moving_group.parent == target_group
assert moving_group.get("halo_parent_uuid") == target_group.get("halo_uuid")
assert tuple(moving_group.halo_node.position) == moving_position
assert tuple(moving_group.halo_node.rotation) == moving_rotation
assert float(moving_group.halo_node.scale) == moving_scale
assert bpy.ops.halo.reparent(target_uuid=definition_root.get("halo_uuid"), preserve_world=False) == {"FINISHED"}
assert moving_group.parent == definition_root
assert moving_group.get("halo_parent_uuid") == definition_root.get("halo_uuid")

# Reparent carry checkboxes default to all-on, but unchecked categories reset
# to schema defaults while selected categories remain local JSON values.
for selected in bpy.context.selected_objects:
    selected.select_set(False)
definition_root.select_set(True)
bpy.context.view_layer.objects.active = definition_root
assert bpy.ops.halo.add_group(group_id="carry_selection_test") == {"FINISHED"}
carry_group = bpy.context.active_object
carry_group.halo_node.position = (1.25, 2.5, 3.75)
carry_group.halo_node.rotation = (11.0, 22.0, 33.0)
carry_group.halo_node.scale = 1.75
carry_group.halo_node.animation_json = json.dumps({"alpha": [{"type": "linear", "start": 0.5, "speed": 0.0}]})
carry_group.halo_node.glowing = False
carry_group.halo_node.inherit_alpha = False
carry_group.halo_node.inherit_glow = False
carry_raw = json.loads(carry_group.get("halo_raw_json", "{}"))
carry_raw["futureCarryField"] = {"drop": True}
carry_group["halo_raw_json"] = json.dumps(carry_raw, ensure_ascii=False, separators=(",", ":"))
assert bpy.ops.halo.reparent(
    target_uuid=target_group.get("halo_uuid"),
    preserve_world=False,
    carry_position=False,
    carry_rotation=True,
    carry_scale=False,
    carry_animation=False,
    carry_render=False,
    carry_extra=False,
) == {"FINISHED"}
assert carry_group.parent == target_group
assert tuple(carry_group.halo_node.position) == (0.0, 0.0, 0.0)
assert max(abs(actual - expected) for actual, expected in zip(carry_group.halo_node.rotation, (11.0, 22.0, 33.0))) < 1e-4
assert float(carry_group.halo_node.scale) == 1.0
assert json.loads(carry_group.halo_node.animation_json) == {}
assert carry_group.halo_node.glowing is True
assert carry_group.halo_node.inherit_alpha is True
assert carry_group.halo_node.inherit_glow is True
assert "futureCarryField" not in json.loads(carry_group.get("halo_raw_json", "{}"))
blender_scene.remove_object_tree(carry_group)
blender_scene.sync_definition_from_scene(scene, renamed_definition_id)

# The tree panel must remain discoverable for root, primitive and empty
# selections.  A selected primitive provides a direct jump to its owning
# group before reparenting.
assert "poll" not in panels.HALO_PT_tree.__dict__
primitive_for_tree = primitives[0]
for selected in bpy.context.selected_objects:
    selected.select_set(False)
primitive_for_tree.select_set(True)
bpy.context.view_layer.objects.active = primitive_for_tree
expected_parent = primitive_for_tree.parent
assert bpy.ops.halo.select_parent_group() == {"FINISHED"}
assert bpy.context.active_object == expected_parent

# Groups and primitives are duplicated as schema-level siblings rather than
# raw Blender object copies.  Every recreated node receives a fresh UUID and
# the copy remains under the same parent.
copy_source_group = top_level_groups[0]
copy_source_raw = json.loads(copy_source_group.get("halo_raw_json", "{}"))
copy_source_raw["futureCopyField"] = {"preserve": True}
copy_source_group["halo_raw_json"] = json.dumps(copy_source_raw, ensure_ascii=False, separators=(",", ":"))
saved_startup_json = definition_item.startup_json
copy_startup = json.loads(saved_startup_json or "{}")
copy_startup.setdefault("id_overrides", {})[copy_source_group.halo_node.node_id] = {
    "segments": [], "futureTransitionField": "copy-me",
}
definition_item.startup_json = json.dumps(copy_startup, ensure_ascii=False, indent=2)
for selected in bpy.context.selected_objects:
    selected.select_set(False)
copy_source_group.select_set(True)
bpy.context.view_layer.objects.active = copy_source_group
assert bpy.ops.halo.duplicate_node() == {"FINISHED"}
group_copy = bpy.context.active_object
assert group_copy.get("halo_role") == "group"
assert group_copy.parent == copy_source_group.parent
assert group_copy.get("halo_uuid") != copy_source_group.get("halo_uuid")
assert group_copy.halo_node.uuid == group_copy.get("halo_uuid")
assert group_copy.halo_node.node_id.startswith((copy_source_group.halo_node.node_id or "group") + "_copy")
assert json.loads(group_copy.get("halo_raw_json", "{}"))["futureCopyField"] == {"preserve": True}
copied_tree = [group_copy] + list(group_copy.children_recursive)
copied_uuids = [item.get("halo_uuid") for item in copied_tree if item.get("halo_role") in {"group", "primitive"}]
assert len(copied_uuids) == len(set(copied_uuids))
original_group_ids = {group.halo_node.node_id for group in groups if group.halo_node.node_id}
copied_group_ids = {item.halo_node.node_id for item in copied_tree if item.get("halo_role") == "group" and item.halo_node.node_id}
assert copied_group_ids.isdisjoint(original_group_ids)
copied_startup = json.loads(definition_item.startup_json)
assert copied_startup["id_overrides"][group_copy.halo_node.node_id]["futureTransitionField"] == "copy-me"
assert raw_contains_group_id(json.loads(definition_item.raw_json).get("layers", []), group_copy.halo_node.node_id)
for copied in copied_tree:
    if copied.get("halo_role") in {"group", "primitive"}:
        assert copied.halo_node.uuid == copied.get("halo_uuid")
blender_scene.remove_object_tree(group_copy)
definition_item.startup_json = saved_startup_json

primitive_copy_source = primitives[0]
source_group = primitive_copy_source.parent
source_group_raw = json.loads(source_group.get("halo_raw_json", "{}"))
source_group_raw["futureMoveField"] = [1, 2, 3]
source_group["halo_raw_json"] = json.dumps(source_group_raw, ensure_ascii=False, separators=(",", ":"))
move_startup = json.loads(saved_startup_json or "{}")
move_startup.setdefault("id_overrides", {})[source_group.halo_node.node_id] = {
    "segments": [], "futureTransitionField": "move-me",
}
definition_item.startup_json = json.dumps(move_startup, ensure_ascii=False, indent=2)
for selected in bpy.context.selected_objects:
    selected.select_set(False)
primitive_copy_source.select_set(True)
bpy.context.view_layer.objects.active = primitive_copy_source
assert bpy.ops.halo.duplicate_node() == {"FINISHED"}
primitive_copy = bpy.context.active_object
assert primitive_copy.get("halo_role") == "primitive"
assert primitive_copy.parent == source_group
assert primitive_copy.get("halo_uuid") != primitive_copy_source.get("halo_uuid")
assert primitive_copy.halo_node.uuid == primitive_copy.get("halo_uuid")
assert blender_scene._sync_primitive(primitive_copy) == blender_scene._sync_primitive(primitive_copy_source)

# Moving a primitive creates a property-only copy of its source group beneath
# the chosen target, then moves only that primitive into the wrapper group.
move_target = next(group for group in groups if group != source_group)
move_items = operators._primitive_move_target_items(None, bpy.context)
move_ids = {item[0] for item in move_items}
assert source_group.get("halo_uuid") not in move_ids
assert move_target.get("halo_uuid") in move_ids
new_group_id = operators._unique_group_id(renamed_definition_id, (source_group.halo_node.node_id or "group") + "_move_test")
assert bpy.ops.halo.move_primitive(target_uuid=move_target.get("halo_uuid"), new_group_id=new_group_id) == {"FINISHED"}
assert bpy.context.active_object == primitive_copy
moved_wrapper = primitive_copy.parent
assert moved_wrapper.get("halo_role") == "group"
assert moved_wrapper.parent == move_target
assert moved_wrapper.halo_node.node_id == new_group_id
assert primitive_copy.get("halo_parent_uuid") == moved_wrapper.get("halo_uuid")
assert primitive_copy.halo_node.parent_uuid == moved_wrapper.get("halo_uuid")
assert [child for child in moved_wrapper.children if child.get("halo_role") == "primitive"] == [primitive_copy]
assert not [child for child in moved_wrapper.children if child.get("halo_role") == "group"]
moved_raw = json.loads(moved_wrapper.get("halo_raw_json", "{}"))
assert moved_raw["futureMoveField"] == [1, 2, 3]
assert list(moved_wrapper.halo_node.position) == list(source_group.halo_node.position)
assert list(moved_wrapper.halo_node.rotation) == list(source_group.halo_node.rotation)
assert float(moved_wrapper.halo_node.scale) == float(source_group.halo_node.scale)
assert moved_wrapper.halo_node.animation_json == source_group.halo_node.animation_json
assert json.loads(definition_item.startup_json)["id_overrides"][new_group_id]["futureTransitionField"] == "move-me"
assert raw_contains_group_id(json.loads(definition_item.raw_json).get("layers", []), new_group_id)
blender_scene.remove_object_tree(moved_wrapper)

# A second migration opts out of every copied category.  The wrapper receives
# schema defaults, drops unknown fields and does not receive id_overrides.
for selected in bpy.context.selected_objects:
    selected.select_set(False)
primitive_copy_source.select_set(True)
bpy.context.view_layer.objects.active = primitive_copy_source
assert bpy.ops.halo.duplicate_node() == {"FINISHED"}
selective_primitive = bpy.context.active_object
selective_group_id = operators._unique_group_id(renamed_definition_id, (source_group.halo_node.node_id or "group") + "_selective_test")
assert bpy.ops.halo.move_primitive(
    target_uuid=move_target.get("halo_uuid"),
    new_group_id=selective_group_id,
    carry_position=False,
    carry_rotation=False,
    carry_scale=False,
    carry_animation=False,
    carry_render=False,
    carry_extra=False,
    carry_transition=False,
) == {"FINISHED"}
selective_wrapper = selective_primitive.parent
assert tuple(selective_wrapper.halo_node.position) == (0.0, 0.0, 0.0)
assert tuple(selective_wrapper.halo_node.rotation) == (0.0, 0.0, 0.0)
assert float(selective_wrapper.halo_node.scale) == 1.0
assert json.loads(selective_wrapper.halo_node.animation_json) == {}
assert selective_wrapper.halo_node.glowing is True
assert selective_wrapper.halo_node.inherit_alpha is True
assert selective_wrapper.halo_node.inherit_glow is True
assert "futureMoveField" not in json.loads(selective_wrapper.get("halo_raw_json", "{}"))
assert selective_group_id not in json.loads(definition_item.startup_json).get("id_overrides", {})
blender_scene.remove_object_tree(selective_wrapper)
definition_item.startup_json = saved_startup_json

# Batch editing accepts only managed siblings.  Copy/delete may mix groups and
# primitives because both remain siblings; moving stays type-specific because
# groups are reparented directly while primitives require one property wrapper.
batch_collection = definition_root.users_collection[0]
batch_parent = blender_scene._make_group(
    batch_collection,
    definition_root,
    {"id": "batch_parent", "position": [0, 0, 0], "rotation": [0, 0, 0], "scale": 1, "children": []},
    renamed_definition_id,
    "test/batch/parent",
)
batch_group_a = blender_scene._make_group(
    batch_collection,
    batch_parent,
    {"id": "batch_group_a", "position": [1, 0, 0], "rotation": [0, 0, 0], "scale": 1, "children": []},
    renamed_definition_id,
    "test/batch/group/a",
)
batch_group_b = blender_scene._make_group(
    batch_collection,
    batch_parent,
    {"id": "batch_group_b", "position": [2, 0, 0], "rotation": [0, 0, 0], "scale": 1, "children": []},
    renamed_definition_id,
    "test/batch/group/b",
)
batch_primitive_raw = json.loads(json.dumps(blender_scene._sync_primitive(primitives[0])))
batch_primitive_a = blender_scene._make_primitive(
    batch_collection,
    batch_parent,
    batch_primitive_raw,
    renamed_definition_id,
    "test/batch/primitive/a",
    True,
)
batch_primitive_b = blender_scene._make_primitive(
    batch_collection,
    batch_parent,
    batch_primitive_raw,
    renamed_definition_id,
    "test/batch/primitive/b",
    True,
)
blender_scene.sync_definition_from_scene(scene, renamed_definition_id)
assert panels._definition_primitive_count(scene, renamed_definition_id) == 31

def select_objects_for_batch(items, active):
    for selected in bpy.context.selected_objects:
        selected.select_set(False)
    for item in items:
        item.select_set(True)
    bpy.context.view_layer.objects.active = active


def assert_batch_operator_rejected(callback):
    try:
        result = callback()
    except RuntimeError:
        # bpy raises reported operator errors in background Python execution;
        # the same report appears as a normal Chinese error in the UI.
        return
    assert result == {"CANCELLED"}


# A mixed sibling selection duplicates and deletes as one batch with new UUIDs.
original_batch_children = set(batch_parent.children)
select_objects_for_batch([batch_group_a, batch_primitive_a], batch_primitive_a)
assert operators._selected_sibling_nodes(bpy.context) == [batch_group_a, batch_primitive_a]
assert bpy.ops.halo.duplicate_node() == {"FINISHED"}
batch_copies = list(bpy.context.selected_objects)
assert len(batch_copies) == 2
assert {obj.get("halo_role") for obj in batch_copies} == {"group", "primitive"}
assert all(obj.parent == batch_parent for obj in batch_copies)
assert all(obj not in original_batch_children for obj in batch_copies)
assert len({obj.get("halo_uuid") for obj in batch_copies}) == 2
assert bpy.ops.halo.delete_node() == {"FINISHED"}
assert bpy.context.active_object == batch_parent
assert set(batch_parent.children) == original_batch_children

# Different parents and mixed-type movement are rejected before any mutation.
select_objects_for_batch([batch_group_a, primitives[0]], batch_group_a)
assert_batch_operator_rejected(lambda: bpy.ops.halo.duplicate_node())
assert batch_group_a.parent == batch_parent
select_objects_for_batch([batch_group_a, batch_primitive_a], batch_group_a)
assert_batch_operator_rejected(
    lambda: bpy.ops.halo.reparent(preserve_world=False)
)
select_objects_for_batch([batch_group_a, batch_primitive_a], batch_primitive_a)
assert_batch_operator_rejected(
    lambda: bpy.ops.halo.move_primitive(
        new_group_id="batch_mixed_rejected",
    )
)

# Two sibling groups move together, remain selected, and can move back.
select_objects_for_batch([batch_group_a, batch_group_b], batch_group_a)
batch_target_ids = {item[0] for item in operators._reparent_target_items(None, bpy.context)}
assert target_group.get("halo_uuid") in batch_target_ids
assert batch_group_a.get("halo_uuid") not in batch_target_ids
assert batch_group_b.get("halo_uuid") not in batch_target_ids
assert bpy.ops.halo.reparent(target_uuid=target_group.get("halo_uuid"), preserve_world=False) == {"FINISHED"}
assert batch_group_a.parent == target_group and batch_group_b.parent == target_group
assert set(bpy.context.selected_objects) == {batch_group_a, batch_group_b}
assert bpy.ops.halo.reparent(target_uuid=batch_parent.get("halo_uuid"), preserve_world=False) == {"FINISHED"}
assert batch_group_a.parent == batch_parent and batch_group_b.parent == batch_parent

# Two primitives from one group migrate into one shared property-copy group.
select_objects_for_batch([batch_primitive_a, batch_primitive_b], batch_primitive_a)
batch_move_id = operators._unique_group_id(renamed_definition_id, "batch_primitives_moved")
assert bpy.ops.halo.move_primitive(
    target_uuid=target_group.get("halo_uuid"),
    new_group_id=batch_move_id,
) == {"FINISHED"}
batch_move_wrapper = batch_primitive_a.parent
assert batch_move_wrapper == batch_primitive_b.parent
assert batch_move_wrapper.parent == target_group
assert batch_move_wrapper.halo_node.node_id == batch_move_id
assert set(bpy.context.selected_objects) == {batch_primitive_a, batch_primitive_b}
assert sorted(int(obj.get("halo_primitive_index", -1)) for obj in (batch_primitive_a, batch_primitive_b)) == [0, 1]

blender_scene.remove_object_tree(batch_move_wrapper)
blender_scene.remove_object_tree(batch_parent)
blender_scene.sync_definition_from_scene(scene, renamed_definition_id)
assert panels._definition_primitive_count(scene, renamed_definition_id) == 29

source_group_raw = json.loads(source_group.get("halo_raw_json", "{}"))
source_group_raw.pop("futureMoveField", None)
source_group["halo_raw_json"] = json.dumps(source_group_raw, ensure_ascii=False, separators=(",", ":"))
copy_source_raw = json.loads(copy_source_group.get("halo_raw_json", "{}"))
copy_source_raw.pop("futureCopyField", None)
copy_source_group["halo_raw_json"] = json.dumps(copy_source_raw, ensure_ascii=False, separators=(",", ":"))
blender_scene.sync_definition_from_scene(scene, renamed_definition_id)

for primitive in primitives:
    kind = primitive.halo_node.primitive_type
    assert primitive.parent and primitive.parent.get("halo_role") == "group"
    assert primitive.data.uv_layers, primitive.name
    if kind == "billboard":
        assert len(primitive.data.vertices) == 4
        assert len(primitive.data.polygons) == 1
        assert primitive.data.polygons[0].normal.z < -0.999
        assert not primitive.data.materials[0].use_backface_culling
        actual_uv = [tuple(round(value, 5) for value in loop.uv) for loop in primitive.data.uv_layers[0].data]
        assert actual_uv == [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)], actual_uv
    elif kind == "ring":
        assert len(primitive.data.polygons) == primitive.halo_node.segments
        assert len(primitive.data.materials) == 1
        assert not primitive.data.materials[0].use_backface_culling
        outer_uv = [tuple(round(value, 5) for value in loop.uv) for loop in primitive.data.uv_layers[0].data[:4]]
        assert outer_uv[0] == (0.0, 1.0) and outer_uv[3] == (0.0, 0.0), outer_uv
    material = primitive.data.materials[0]
    assert material.surface_render_method == "DITHERED"
    assert material.get("halo_preview_render_method") == "EEVEE_DITHERED"
    assert not material.use_transparency_overlap
    assert not material.show_transparent_back
    assert any(node.name == "Halo Preview Alpha" for node in material.node_tree.nodes)
    paired_sides = bool(primitive.halo_node.inner_texture)
    assert bool(material.get("halo_shader_side_selection")) == paired_sides
    assert bool(material.node_tree.nodes.get("Halo Backface Mix")) == paired_sides
    assert material.node_tree.nodes.get("Halo Visibility Light Path") is not None
    assert material.node_tree.nodes.get("Halo Visibility Mix") is not None
    assert material.get("halo_camera_only_shader") is True

legacy_material = primitives[0].data.materials[0]
legacy_material.surface_render_method = "BLENDED"

# Simulate an explicit-inner Ring saved by 0.1.15: two coincident surfaces and
# two culled material slots.  Refresh must collapse it to the 0.1.17 one-surface
# representation while retaining both JSON texture identifiers.
legacy_ring = next(item for item in primitives if item.halo_node.primitive_type == "ring" and item.halo_node.inner_texture)
old_mesh = legacy_ring.data
legacy_ring.data = ring_mesh(legacy_ring.name + " Legacy", legacy_ring.halo_node.size, legacy_ring.halo_node.segments, True)
if old_mesh.users == 0:
    bpy.data.meshes.remove(old_mesh)
legacy_outer = create_halo_material(legacy_ring.halo_node.texture, scene.halo_project.pack_root, backface_culling=True, name="Legacy Outer")
legacy_inner = create_halo_material(legacy_ring.halo_node.inner_texture, scene.halo_project.pack_root, backface_culling=True, name="Legacy Inner")
assign_material(legacy_ring, legacy_outer, 0)
assign_material(legacy_ring, legacy_inner, 1)
assert len(legacy_ring.data.polygons) == legacy_ring.halo_node.segments * 2
assert len(legacy_ring.data.materials) == 2
assert bpy.ops.halo.configure_eevee_preview() == {"FINISHED"}
assert scene.render.engine == "BLENDER_EEVEE"
assert legacy_material.surface_render_method == "DITHERED"
assert len(legacy_ring.data.polygons) == legacy_ring.halo_node.segments
assert len(legacy_ring.data.materials) == 1
assert legacy_ring.data.materials[0].get("halo_shader_backface_mode") == "PAIRED_TEXTURE"

# Ring outer/inner textures have separate file-picker targets.  Clearing the
# inner texture returns to one double-sided surface; importing an inner PNG
# recreates the inner surface and material slot immediately.
ring = next(item for item in primitives if item.halo_node.primitive_type == "ring")
alternate = next(item for item in primitives if item.halo_node.texture and item.halo_node.texture != ring.halo_node.texture)
alternate_path = resolve_texture_path(alternate.halo_node.texture, scene.halo_project.pack_root)
assert alternate_path is not None
for selected in bpy.context.selected_objects:
    selected.select_set(False)
ring.select_set(True)
bpy.context.view_layer.objects.active = ring
assert bpy.ops.halo.clear_inner_texture() == {"FINISHED"}
assert ring.halo_node.inner_texture == ""
assert len(ring.data.materials) == 1
assert len(ring.data.polygons) == ring.halo_node.segments
assert bpy.ops.halo.import_texture(filepath=alternate_path, target="INNER") == {"FINISHED"}
assert ring.halo_node.inner_texture
assert len(ring.data.materials) == 1
assert ring.data.materials[0].get("halo_backface_texture_id") == ring.halo_node.inner_texture
assert len(ring.data.polygons) == ring.halo_node.segments
assert ring.data.materials[0].get("halo_shader_backface_mode") == "PAIRED_TEXTURE"

# Typed MC transform -> Blender object -> exported JSON.
group = groups[0]
group.halo_node.position = (1.25, 2.5, -3.75)
assert tuple(round(v, 5) for v in group.location) == (1.25, 3.75, 2.5)
group.halo_node.rotation = (25.0, -10.0, 5.0)
group.halo_node.scale = 1.2
assert all(abs(float(value) - 1.2) < 1e-6 for value in group.scale)
typed_raw = json.loads(group.get("halo_raw_json", "{}"))
assert typed_raw["position"] == [1.25, 2.5, -3.75]
assert typed_raw["rotation"] == [25.0, -10.0, 5.0]
assert abs(typed_raw["scale"] - 1.2) < 1e-5

# Static JSON transforms are panel-owned.  Managed objects are locked against
# native G/R/S, and even a scripted object move cannot replace the typed value
# during synchronization/export.
assert all(group.lock_location) and all(group.lock_rotation) and all(group.lock_scale)
group.location = (99.0, 98.0, 97.0)
blender_scene.sync_definition_from_scene(scene, group.get("halo_definition_id"))
assert tuple(round(v, 5) for v in group.halo_node.position) == (1.25, 2.5, -3.75)
assert tuple(round(v, 5) for v in group.location) == (1.25, 3.75, 2.5)

# Coarse/fine nudge controls update the same authoritative panel fields.
for selected in bpy.context.selected_objects:
    selected.select_set(False)
group.select_set(True)
bpy.context.view_layer.objects.active = group
scene.halo_project.transform_precision = "FINE"
scene.halo_project.fine_position_step = 0.01
assert bpy.ops.halo.nudge_transform(target="position", axis=0, direction=1) == {"FINISHED"}
assert abs(group.halo_node.position[0] - 1.26) < 1e-5

handle = next(item for item in primitives if not item.get("halo_face_camera"))
owner = handle.parent
assert all(handle.lock_location) and all(handle.lock_rotation) and all(handle.lock_scale)

# Primitive typed fields now refresh the preview without requiring the manual
# geometry button; the button remains as an explicit recovery action.
billboard = next(item for item in primitives if item.halo_node.primitive_type == "billboard")
old_mesh = billboard.data
old_size = tuple(billboard.halo_node.size)
billboard.halo_node.size = (old_size[0] + 0.125, old_size[1] + 0.25)
assert billboard.data != old_mesh
assert len(billboard.data.vertices) == 4
owner_position = tuple(billboard.parent.halo_node.position)
billboard.halo_node.face_camera = True
assert billboard.get("halo_face_camera") is True
billboard.location = (2.0, 3.0, 4.0)
billboard.rotation_mode = "QUATERNION"
billboard.rotation_quaternion = Quaternion((0.5, 0.5, 0.5, 0.5))
billboard.halo_node.face_camera = False
assert billboard.get("halo_face_camera") is False
assert billboard.location.length < 1e-8
assert billboard.rotation_quaternion.rotation_difference(Quaternion()).angle < 1e-8
assert tuple(billboard.parent.halo_node.position) == owner_position

# 强制刷新 performs the same full re-import/reset repair.
for selected in bpy.context.selected_objects:
    selected.select_set(False)
billboard.select_set(True)
bpy.context.view_layer.objects.active = billboard
billboard.location = (5.0, 6.0, 7.0)
billboard.scale = (2.0, 2.0, 2.0)
assert bpy.ops.halo.refresh_geometry() == {"FINISHED"}
assert billboard.location.length < 1e-8
assert (billboard.scale - Vector((1.0, 1.0, 1.0))).length < 1e-8

# A face-camera primitive must keep the most recently drawn viewport
# orientation while its parent group is animated on the timeline.  This is a
# headless equivalent of rotating the 3D View and pressing Play.
face_camera_owner = billboard.parent
original_face_camera_animation = face_camera_owner.halo_node.animation_json
original_face_camera_animation_raw = face_camera_owner.get("halo_animation_json", "{}")
original_preview_mode = scene.halo_project.preview_mode
original_frame = scene.frame_current
viewport_quaternion = Quaternion((0.9238795325, 0.0, 0.3826834324, 0.0))
face_camera_animation = json.dumps({
    "rotation": {
        "yaw": [{"function": "linear", "start": 0.0, "speed": 45.0}],
        "pitch": [{"function": "linear", "start": 5.0, "speed": 0.0}],
        "roll": [{"function": "linear", "start": 0.0, "speed": 0.0}],
    },
})
face_camera_owner.halo_node.animation_json = face_camera_animation
face_camera_owner["halo_animation_json"] = face_camera_animation
billboard.halo_node.face_camera = True
handlers._apply_face_camera_orientation(scene, viewport_quaternion, remember_view=True)
scene.halo_project.preview_mode = "IDLE"
parent_rotations = []
for preview_frame in (10, 30):
    scene.frame_set(preview_frame)
    handlers.update_animation(scene)
    bpy.context.view_layer.update()
    parent_rotations.append(face_camera_owner.matrix_world.to_quaternion().copy())
    actual_facing = billboard.matrix_world.to_quaternion()
    assert actual_facing.rotation_difference(viewport_quaternion).angle < 1e-5
assert parent_rotations[0].rotation_difference(parent_rotations[1]).angle > 0.1
billboard.halo_node.face_camera = False
face_camera_owner.halo_node.animation_json = original_face_camera_animation
face_camera_owner["halo_animation_json"] = original_face_camera_animation_raw
handlers._SCENE_VIEW_QUATERNIONS.pop(handlers._scene_key(scene), None)
scene.halo_project.preview_mode = original_preview_mode
scene.frame_set(original_frame)
handlers.update_animation(scene)

owner.halo_node.glowing = False
assert json.loads(owner.get("halo_raw_json", "{}"))["glowing"] is False

scene.halo_project.preview_mode = "IDLE"
scene.frame_set(20)
handlers.update_animation(scene)
assert hasattr(group, "delta_location")

# Definition/root animation is local to the head anchor in Minecraft.  Verify
# the complete renderer order T(anchor) R(anchor) S(positioning) * T/R/S(anim)
# with a deliberately rotated head and non-unit positioning scale.  This also
# proves the old world-axis delta_location behavior is gone.
original_animation_json = definition_item.animation_json
original_preview_space = scene.halo_project.preview_space
original_head_pose = (
    scene.halo_project.head_yaw,
    scene.halo_project.head_pitch,
    scene.halo_project.head_roll,
)
original_positioning_offset = tuple(definition_item.positioning_offset)
original_positioning_scale = definition_item.positioning_scale
original_orientation = definition_item.orientation_mode
definition_item.animation_json = json.dumps({
    "offset": {
        "x": [{"function": "linear", "start": 1.0, "speed": 0.0}],
        "y": [{"function": "linear", "start": 2.0, "speed": 0.0}],
        "z": [{"function": "linear", "start": -0.5, "speed": 0.0}],
    },
    "rotation": {
        "yaw": [{"function": "linear", "start": 27.0, "speed": 0.0}],
        "pitch": [{"function": "linear", "start": -13.0, "speed": 0.0}],
        "roll": [{"function": "linear", "start": 8.0, "speed": 0.0}],
    },
}, ensure_ascii=False)
definition_item.positioning_offset = (0.4, 0.8, -0.3)
definition_item.positioning_scale = 1.75
definition_item.orientation_mode = "locked"
scene.halo_project.preview_space = "MC_HEAD"
scene.halo_project.head_yaw = 41.0
scene.halo_project.head_pitch = -18.0
scene.halo_project.head_roll = 12.0
blender_scene.update_preview_roots(scene)
base_location = Vector(definition_root["halo_preview_base_location"])
base_rotation = Quaternion(definition_root["halo_preview_base_rotation"])
base_scale = Vector(definition_root["halo_preview_base_scale"])
handlers.update_animation(scene)
local_offset = Vector(mc_to_blender((1.0, 2.0, -0.5)))
scaled_offset = Vector(tuple(local_offset[index] * base_scale[index] for index in range(3)))
expected_location = base_location + base_rotation @ scaled_offset
expected_rotation = base_rotation @ mc_rotation_quaternion((27.0, -13.0, 8.0))
assert (definition_root.location - expected_location).length < 1e-5
assert definition_root.rotation_quaternion.rotation_difference(expected_rotation).angle < 1e-5
world_axis_location = base_location + scaled_offset
assert (definition_root.location - world_axis_location).length > 0.1
assert definition_root.delta_location.length < 1e-8

# Restore the imported fixture before round-trip/export assertions.
definition_item.animation_json = original_animation_json
definition_item.positioning_offset = original_positioning_offset
definition_item.positioning_scale = original_positioning_scale
definition_item.orientation_mode = original_orientation
scene.halo_project.preview_space = original_preview_space
(
    scene.halo_project.head_yaw,
    scene.halo_project.head_pitch,
    scene.halo_project.head_roll,
) = original_head_pose
blender_scene.update_preview_roots(scene)
handlers.update_animation(scene)

validation = operators.validate_scene(scene)
assert not validation["errors"], json.dumps(validation, ensure_ascii=False)

exported = cache_root / "hina-roundtrip.zip"
blender_scene.export_pack_from_scene(scene, exported, zip_output=True, overwrite=True)
roundtrip = import_pack(exported)
assert len(roundtrip.definitions) == 1
assert roundtrip.definitions[0].identifier == renamed_definition_id
roundtrip_damping = roundtrip.definitions[0].raw["damping"]
assert roundtrip_damping["linearFactor"] == 0.0
assert roundtrip_damping["maxLinearDistance"] == 0.0
assert roundtrip_damping["futureDampingField"] == {"keep": True}
exported_folder = cache_root / "hina-roundtrip-folder"
blender_scene.export_pack_from_scene(scene, exported_folder, zip_output=False, overwrite=True)
folder_roundtrip = import_pack(exported_folder)
assert len(folder_roundtrip.definitions) == 1

blend_path = cache_root / "hina-editor.blend"
bpy.ops.wm.save_as_mainfile(filepath=str(blend_path))

# Transition/id_overrides and face_camera integration fixtures.
shiroko = Path(r"F:\HaloPackTool\output\Individual\Shiroko.zip")
blender_scene.import_project_to_scene(bpy.context, shiroko, replace=True)
scene.halo_project.preview_mode = "STARTUP"
scene.frame_set(8)
handlers.update_animation(scene)
assert any(obj.get("halo_role") == "group" for obj in bpy.data.objects)

# Graphical transition editor: choose a real group ID, create an override,
# manage ordered segments and edit per-channel endpoints/overrides without
# losing unrelated JSON.  Boundary requirements follow the Java parser:
# startup's first active property needs from; shutdown's last needs to.
active_definition = next(item for item in scene.halo_project.definitions if item.definition_id == scene.halo_project.active_definition)
startup_payload = json.loads(active_definition.startup_json or "{}")
startup_payload["editor_unknown_top_level"] = {"preserve": True}
active_definition.startup_json = json.dumps(startup_payload, ensure_ascii=False, indent=2)
existing_overrides = startup_payload.get("id_overrides", {})
graphical_group = next(obj for obj in bpy.data.objects if obj.get("halo_role") == "group" and obj.halo_node.node_id)
original_graphical_group_id = graphical_group.halo_node.node_id
graphical_group_id = original_graphical_group_id + "_editor_transition_test"
assert graphical_group_id not in existing_overrides
graphical_group.halo_node.node_id = graphical_group_id
scene.halo_project.transition_target = "startup"
scene.halo_project.transition_group_id = graphical_group_id
assert bpy.ops.halo.transition_segment_add(duration=0.4, easing="ease_out_cubic") == {"FINISHED"}
assert bpy.ops.halo.transition_segment_add(duration=0.6, easing="linear") == {"FINISHED"}
assert bpy.ops.halo.transition_segment_edit(index=1, duration=0.75, easing="ease_in_out_cubic") == {"FINISHED"}

# The first active startup rotation channel rejects a missing from endpoint.
try:
    bpy.ops.halo.transition_channel_edit(
        index=0, channel="rotation", has_from=False, has_to=True,
        to_value=(20.0, 0.0, 0.0), use_degrees=True, degrees=(360.0, 0.0, 0.0),
    )
except RuntimeError as exc:
    assert "必须填写 from" in str(exc)
else:
    raise AssertionError("startup boundary without from should be rejected")
assert bpy.ops.halo.transition_channel_edit(
    index=0, channel="rotation", has_from=True, from_value=(0.0, 0.0, 0.0),
    has_to=False, use_duration=True, duration=0.25,
    use_easing=True, easing="ease_out_cubic", use_degrees=True, degrees=(360.0, 0.0, 0.0),
) == {"FINISHED"}
# A later segment may inherit from the preceding end.
assert bpy.ops.halo.transition_channel_edit(
    index=1, channel="rotation", has_from=False, has_to=True,
    to_value=(45.0, -10.0, 5.0), use_degrees=False,
) == {"FINISHED"}
edited_startup = json.loads(active_definition.startup_json)
assert edited_startup["editor_unknown_top_level"] == {"preserve": True}
edited_override = edited_startup["id_overrides"][graphical_group_id]
edited_segments = edited_override["segments"] if isinstance(edited_override, dict) else edited_override
assert len(edited_segments) == 2
assert edited_segments[0]["rotation"]["degrees"] == [360.0, 0.0, 0.0]
assert edited_segments[0]["rotation"]["duration"] == 0.25
assert "from" not in edited_segments[1]["rotation"]
assert edited_segments[1]["rotation"]["to"] == [45.0, -10.0, 5.0]
# Reordering would make the inherited-from segment the first active segment,
# so the editor rejects it until an explicit boundary is authored.
try:
    bpy.ops.halo.transition_segment_move(index=1, direction=-1)
except RuntimeError as exc:
    assert "缺少必填 from" in str(exc)
else:
    raise AssertionError("moving an inherited startup boundary to the front should be rejected")
assert bpy.ops.halo.transition_channel_edit(
    index=1, channel="rotation", has_from=True, from_value=(0.0, 0.0, 0.0),
    has_to=True, to_value=(45.0, -10.0, 5.0),
) == {"FINISHED"}
assert bpy.ops.halo.transition_segment_move(index=1, direction=-1) == {"FINISHED"}
moved_startup = json.loads(active_definition.startup_json)
moved_override = moved_startup["id_overrides"][graphical_group_id]
moved_segments = moved_override["segments"] if isinstance(moved_override, dict) else moved_override
assert moved_segments[0]["duration"] == 0.75
assert bpy.ops.halo.transition_segment_move(index=0, direction=1) == {"FINISHED"}
assert bpy.ops.halo.transition_segment_remove(index=1) == {"FINISHED"}
trimmed_startup = json.loads(active_definition.startup_json)
trimmed_override = trimmed_startup["id_overrides"][graphical_group_id]
trimmed_segments = trimmed_override["segments"] if isinstance(trimmed_override, dict) else trimmed_override
assert len(trimmed_segments) == 1

# Shutdown uses the opposite authored boundary: the final active channel must
# provide to, while from may inherit the hide-moment resident value.
scene.halo_project.transition_target = "shutdown"
scene.halo_project.transition_group_id = graphical_group_id
assert bpy.ops.halo.transition_segment_add(duration=0.35, easing="linear") == {"FINISHED"}
try:
    bpy.ops.halo.transition_channel_edit(
        index=0, channel="alpha", has_from=True, scalar_from=1.0, has_to=False,
    )
except RuntimeError as exc:
    assert "必须填写 to" in str(exc)
else:
    raise AssertionError("shutdown boundary without to should be rejected")
assert bpy.ops.halo.transition_channel_edit(
    index=0, channel="alpha", has_from=False, has_to=True, scalar_to=0.0,
    use_easing=True, easing="ease_in_out_cubic",
) == {"FINISHED"}
edited_shutdown = json.loads(active_definition.shutdown_json)
shutdown_override = edited_shutdown["id_overrides"][graphical_group_id]
shutdown_segments = shutdown_override["segments"] if isinstance(shutdown_override, dict) else shutdown_override
assert shutdown_segments[0]["alpha"]["to"] == 0.0
assert shutdown_segments[0]["alpha"]["easing"] == "ease_in_out_cubic"
assert bpy.ops.halo.transition_channel_remove(index=0, channel="alpha") == {"FINISHED"}
assert bpy.ops.halo.transition_override_clear() == {"FINISHED"}
assert graphical_group_id not in json.loads(active_definition.shutdown_json).get("id_overrides", {})
scene.halo_project.transition_target = "startup"
scene.halo_project.transition_group_id = graphical_group_id
assert bpy.ops.halo.transition_override_clear() == {"FINISHED"}
graphical_group.halo_node.node_id = original_graphical_group_id
scene.halo_project.transition_group_id = "__HALO_DEFAULT__"

# The graphical resident editor exposes existing channel terms and edits a
# row directly; no user-visible numeric "term index" is required.
channel_names = (
    "offset.x", "offset.y", "offset.z", "rotation.yaw", "rotation.pitch",
    "rotation.roll", "scale.x", "scale.y", "scale.z", "alpha", "glow",
)
animated_group = None
animated_channel = None
for candidate in (obj for obj in bpy.data.objects if obj.get("halo_role") == "group"):
    animation = json.loads(candidate.halo_node.animation_json or "{}")
    for channel in channel_names:
        block, dot, axis = channel.partition(".")
        terms = animation.get(block, {}).get(axis, []) if dot else animation.get(block, [])
        if isinstance(terms, list) and terms:
            animated_group, animated_channel = candidate, channel
            break
    if animated_group is not None:
        break
assert animated_group is not None and animated_channel is not None
for selected in bpy.context.selected_objects:
    selected.select_set(False)
animated_group.select_set(True)
bpy.context.view_layer.objects.active = animated_group
scene.halo_project.animation_channel = animated_channel
animation = json.loads(animated_group.halo_node.animation_json)
block, dot, axis = animated_channel.partition(".")
terms = animation[block][axis] if dot else animation[block]
original_count = len(terms)
assert bpy.ops.halo.animation_term_add(function="linear", start=2.0, speed=3.0) == {"FINISHED"}
animation = json.loads(animated_group.halo_node.animation_json)
terms = animation[block][axis] if dot else animation[block]
assert len(terms) == original_count + 1 and terms[-1]["speed"] == 3.0
assert bpy.ops.halo.animation_term_edit(index=original_count, function="cos", amplitude=4.0, omega=5.0, phi=6.0) == {"FINISHED"}
animation = json.loads(animated_group.halo_node.animation_json)
terms = animation[block][axis] if dot else animation[block]
assert terms[-1]["function"] == "cos" and terms[-1]["A"] == 4.0
assert bpy.ops.halo.animation_term_remove(index=original_count) == {"FINISHED"}

# Startup/shutdown JSON opens as a real multiline Text datablock and can be
# applied back to the definition.
assert bpy.ops.halo.open_animation_json(target="startup") == {"FINISHED"}
animation_text = bpy.data.texts[scene.halo_project.animation_text_name]
startup_payload = json.loads(animation_text.as_string())
startup_payload["editor_roundtrip_test"] = True
animation_text.clear()
animation_text.write(json.dumps(startup_payload, ensure_ascii=False, indent=2))
assert bpy.ops.halo.apply_animation_json() == {"FINISHED"}
active_definition = next(item for item in scene.halo_project.definitions if item.definition_id == scene.halo_project.active_definition)
assert json.loads(active_definition.startup_json)["editor_roundtrip_test"] is True

mod_resources = Path(r"F:\Halo\src\main\resources")
blender_scene.import_project_to_scene(bpy.context, mod_resources, replace=True)
face_camera_objects = [obj for obj in bpy.data.objects if obj.get("halo_face_camera")]
assert face_camera_objects
assert handlers._VIEW_DRAW_HANDLE is not None
print(json.dumps({
    "definitions": 1,
    "groups": len(groups),
    "primitives": len(primitives),
    "warnings": len(validation["warnings"]),
    "roundtrip": str(exported),
    "folder_roundtrip": str(exported_folder),
    "blend": str(blend_path),
    "face_camera": len(face_camera_objects),
}, ensure_ascii=False))

halo_pack_editor.unregister()
