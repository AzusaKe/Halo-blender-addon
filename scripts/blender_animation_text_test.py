"""Multi-line resident/transition Text edits survive save, reopen and export."""

import json
import importlib
import os
from pathlib import Path
import sys
import tempfile
import zipfile

import bpy

project_root = Path(sys.argv[sys.argv.index("--") + 1]).resolve()
sys.path.insert(0, str(project_root))
cache = Path(r"F:\codex-cache\halo-blender-addon\animation-text-tests")
cache.mkdir(parents=True, exist_ok=True)
test_root = Path(tempfile.mkdtemp(prefix="text_", dir=cache))
os.environ["BLENDER_USER_DATAFILES"] = str(test_root / "datafiles")

arguments = sys.argv[sys.argv.index("--") + 1:]
module_name = arguments[1] if len(arguments) > 1 else "halo_pack_editor"
halo_pack_editor = importlib.import_module(module_name)
blender_scene = importlib.import_module(module_name + ".blender_scene")
text_is_pending = importlib.import_module(module_name + ".animation_text").text_is_pending

if not hasattr(bpy.types.Scene, "halo_project"):
    halo_pack_editor.register()
assert bpy.ops.halo.new_project() == {"FINISHED"}
scene = bpy.context.scene
project = scene.halo_project
item = project.definitions[0]
root = next(obj for obj in scene.objects if obj.get("halo_role") == "definition_root")
group = next(obj for obj in scene.objects if obj.get("halo_role") == "group")


def activate(obj):
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    project.active_definition = obj.get("halo_definition_id", item.definition_id)


def edit(target, owner, document, *, apply="operator"):
    activate(owner)
    assert bpy.ops.halo.open_animation_json(target=target) == {"FINISHED"}
    text = bpy.data.texts[project.animation_text_name]
    text.clear()
    text.write(json.dumps(document, ensure_ascii=False, indent=2))
    assert text_is_pending(text)
    # Reopening must never discard a pending edit.
    assert bpy.ops.halo.open_animation_json(target=target) == {"FINISHED"}
    assert json.loads(text.as_string()) == document
    if apply == "operator":
        assert bpy.ops.halo.apply_animation_json() == {"FINISHED"}
        assert not text_is_pending(text)
    elif apply == "return":
        assert bpy.ops.halo.return_3d_view() == {"FINISHED"}
        assert not text_is_pending(text)
    return text


root_animation = {
    "offset": {"x": [{"function": "linear", "start": 1.25, "speed": 0.0}]},
    "editor_root_marker": True,
}
group_animation = {
    "rotation": {"roll": [{"function": "sin", "A": 12.0, "omega": 2.0, "phi": 0.5}]},
    "editor_group_marker": True,
}
startup = {"segments": [{"duration": 0.4, "offset": {"from": [0, -1, 0], "to": [0, 0, 0]}}],
           "editor_startup_marker": True}
shutdown = {"segments": [{"duration": 0.3, "alpha": {"from": 1.0, "to": 0.0}}],
            "editor_shutdown_marker": True}

edit("resident", root, root_animation)
edit("resident", group, group_animation, apply="return")
edit("startup", root, startup)
shutdown_text = edit("shutdown", root, shutdown, apply="pending")

# Pending valid JSON applies automatically during normal .blend save.
blend_path = test_root / "animation-text.blend"
bpy.ops.wm.save_as_mainfile(filepath=str(blend_path))
assert not text_is_pending(shutdown_text)
bpy.ops.wm.open_mainfile(filepath=str(blend_path))
scene = bpy.context.scene
project = scene.halo_project
item = project.definitions[0]
root = next(obj for obj in scene.objects if obj.get("halo_role") == "definition_root")
group = next(obj for obj in scene.objects if obj.get("halo_role") == "group")
assert json.loads(item.animation_json) == root_animation
assert json.loads(item.startup_json) == startup
assert json.loads(item.shutdown_json) == shutdown
assert json.loads(group.halo_node.animation_json) == group_animation
root_raw, group_raw = json.loads(root["halo_raw_json"]), json.loads(group["halo_raw_json"])
assert root_raw["animation"] == root_animation and root_raw["startup"] == startup and root_raw["shutdown"] == shutdown
assert group_raw["animation"] == group_animation

# Export auto-applies another pending edit before synchronizing scene JSON.
startup["editor_export_autosave"] = 36
startup_text = edit("startup", root, startup, apply="pending")
output = test_root / "animation-text.zip"
blender_scene.export_pack_from_scene(scene, output, zip_output=True)
assert not text_is_pending(startup_text)
with zipfile.ZipFile(output) as archive:
    documents = [json.loads(archive.read(name)) for name in archive.namelist()
                 if "/halo_definitions/" in name and name.endswith(".json")]
exported = next(document for document in documents if document["id"] == item.definition_id)
assert exported["animation"] == root_animation
assert exported["startup"] == startup
assert exported["shutdown"] == shutdown
assert exported["layers"][0]["animation"] == group_animation

# Definition renaming keeps every open editor (including pending text) bound.
shutdown["editor_after_id_rename"] = True
shutdown_text = edit("shutdown", root, shutdown, apply="pending")
item.definition_id = "minecraft:animation_text_renamed"
assert shutdown_text["halo_definition_id"] == item.definition_id
bpy.ops.wm.save_as_mainfile(filepath=str(test_root / "renamed-animation-text.blend"))
assert json.loads(item.shutdown_json) == shutdown and not text_is_pending(shutdown_text)

# Invalid text stays editable and cannot silently replace/export valid data.
startup_text.clear()
startup_text.write('{"segments": [}')
project.animation_text_name = startup_text.name
assert text_is_pending(startup_text)
try:
    result = bpy.ops.halo.apply_animation_json()
    assert result == {"CANCELLED"}
except RuntimeError as exc:
    assert "动画 JSON 无法解析" in str(exc)
try:
    blender_scene.export_pack_from_scene(scene, test_root / "invalid.zip", zip_output=True)
    raise AssertionError("invalid pending animation unexpectedly exported")
except ValueError as exc:
    assert "动画 JSON 尚未应用" in str(exc)
assert json.loads(item.startup_json) == startup
assert startup_text.as_string() == '{"segments": [}'

print("ANIMATION_TEXT_PERSISTENCE_OK", blend_path, output)
halo_pack_editor.unregister()
