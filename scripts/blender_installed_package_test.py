"""Install-from-disk smoke test using Blender's real extension module path."""

import importlib
from pathlib import Path
import shutil
import sys
import tempfile

import bpy


serina_pack = Path(sys.argv[sys.argv.index("--") + 1]).resolve()
addon = importlib.import_module("bl_ext.user_default.halo_pack_editor")
addon.register()
blender_scene = importlib.import_module("bl_ext.user_default.halo_pack_editor.blender_scene")

scene = bpy.context.scene
bpy.ops.halo.new_project(with_default_halo=True)
item = scene.halo_project.definitions[0]
item.definition_id = "trinity:installed"
primitive = next(obj for obj in scene.objects if obj.get("halo_role") == "primitive")
bpy.context.view_layer.objects.active = primitive
primitive.select_set(True)

scratch = Path(tempfile.mkdtemp(prefix="halo_installed_test_"))
try:
    png_path = scratch / "installed.png"
    image = bpy.data.images.new("Installed Package PNG", width=1, height=1, alpha=True)
    image.pixels[:] = [0.2, 0.4, 0.8, 1.0]
    image.filepath_raw = str(png_path)
    image.file_format = "PNG"
    image.save()
    bpy.data.images.remove(image)
    assert bpy.ops.halo.import_texture(filepath=str(png_path), target="OUTER") == {"FINISHED"}
    assert primitive.halo_node.texture == "trinity:textures/halo/installed.png"

    item.source_path = "assets/minecraft/halo_definitions/halo.json"
    exported = scratch / "exported"
    blender_scene.export_pack_from_scene(scene, exported, zip_output=False)
    assert (exported / "assets/trinity/halo_definitions/installed.json").is_file()
    assert not (exported / "assets/trinity/halo_definitions/halo.json").exists()

    data = blender_scene.import_project_to_scene(bpy.context, serina_pack, replace=True)
    assert data["definitions"] and not data["definitions"][0]["error"]
    group = next(obj for obj in scene.objects if obj.get("halo_role") == "group" and obj.halo_node.node_id == "serina")
    scene.render.fps = 20
    scene.halo_project.preview_mode = "IDLE"
    scene.frame_set(1)
    start = tuple(group.delta_location)
    scene.frame_set(21)
    end = tuple(group.delta_location)
    assert start != end and abs(end[2] - 0.02) < 1e-5, (start, end)
finally:
    addon.unregister()
    shutil.rmtree(scratch, ignore_errors=True)

print("INSTALLED_PACKAGE_OK")
