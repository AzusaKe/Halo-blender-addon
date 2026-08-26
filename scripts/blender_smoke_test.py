"""Blender 5.2 background integration test for Halo Pack Editor."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import bpy


project_root = Path(sys.argv[sys.argv.index("--") + 1]).resolve()
cache_root = Path(r"F:\codex-cache\halo-blender-addon\tests")
cache_root.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(project_root))

import halo_pack_editor
from halo_pack_editor import blender_scene, handlers, operators
from halo_pack_editor.core.pack_io import import_pack


halo_pack_editor.register()
scene = bpy.context.scene
source = Path(r"F:\HaloPackTool\output\Individual\Hina.zip")
data = blender_scene.import_project_to_scene(bpy.context, source, replace=True)
assert len(data["definitions"]) == 1

objects = [obj for obj in bpy.data.objects if obj.get("halo_definition_id")]
groups = [obj for obj in objects if obj.get("halo_role") == "group"]
primitives = [obj for obj in objects if obj.get("halo_role") == "primitive"]
assert len(groups) == 57, len(groups)
assert len(primitives) == 29, len(primitives)

def group_depth(obj):
    depth = 0
    while obj.parent and obj.parent.get("halo_role") == "group":
        depth += 1
        obj = obj.parent
    return depth

assert max(map(group_depth, groups)) >= 3
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
        expected_surfaces = 2 if primitive.halo_node.inner_texture else 1
        assert len(primitive.data.polygons) == primitive.halo_node.segments * expected_surfaces
        assert primitive.data.materials[0].use_backface_culling == bool(primitive.halo_node.inner_texture)
        outer_uv = [tuple(round(value, 5) for value in loop.uv) for loop in primitive.data.uv_layers[0].data[:4]]
        assert outer_uv[0] == (0.0, 1.0) and outer_uv[3] == (0.0, 0.0), outer_uv
    material = primitive.data.materials[0]
    assert material.surface_render_method == "BLENDED"
    assert not material.use_transparency_overlap
    assert not material.show_transparent_back
    assert any(node.name == "Halo Preview Alpha" for node in material.node_tree.nodes)

# Typed MC transform -> Blender object -> exported JSON.
group = groups[0]
group.halo_node.position = (1.25, 2.5, -3.75)
assert tuple(round(v, 5) for v in group.location) == (1.25, 3.75, 2.5)

scene.halo_project.preview_mode = "IDLE"
scene.frame_set(20)
handlers.update_animation(scene)
assert hasattr(group, "delta_location")

validation = operators.validate_scene(scene)
assert not validation["errors"], json.dumps(validation, ensure_ascii=False)

exported = cache_root / "hina-roundtrip.zip"
blender_scene.export_pack_from_scene(scene, exported, zip_output=True, overwrite=True)
roundtrip = import_pack(exported)
assert len(roundtrip.definitions) == 1
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
animation_text = bpy.data.texts[scene.halo_project.raw_text_name]
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
