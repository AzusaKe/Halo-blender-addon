"""Regression for ID-derived texture namespaces and definition filenames."""

import json
from pathlib import Path
import shutil
import sys
import tempfile

import bpy


project_root = Path(sys.argv[sys.argv.index("--") + 1]).resolve()
sys.path.insert(0, str(project_root))

import halo_pack_editor
from halo_pack_editor import blender_scene, operators


halo_pack_editor.register()
scene = bpy.context.scene
bpy.ops.halo.new_project(with_default_halo=True)
item = scene.halo_project.definitions[0]
item.definition_id = "trinity:serina"
assert scene.halo_project.active_definition == "trinity:serina"

primitive = next(
    obj for obj in scene.objects
    if obj.get("halo_role") == "primitive" and obj.get("halo_definition_id") == "trinity:serina"
)
bpy.context.view_layer.objects.active = primitive
primitive.select_set(True)

scratch = Path(tempfile.mkdtemp(prefix="halo_id_resource_"))
try:
    png_path = scratch / "serina_detail.png"
    image = bpy.data.images.new("ID Namespace Test", width=2, height=2, alpha=True)
    image.pixels[:] = [1.0, 0.0, 0.0, 1.0] * 4
    image.filepath_raw = str(png_path)
    image.file_format = "PNG"
    image.save()
    bpy.data.images.remove(image)

    result = bpy.ops.halo.import_texture(filepath=str(png_path), target="OUTER")
    assert result == {"FINISHED"}, result
    first_id = primitive.halo_node.texture
    assert first_id == "trinity:textures/halo/serina_detail.png", first_id
    assert ":" in first_id and not first_id.startswith("minecraft:"), first_id

    # Importing exactly the same material family reuses the existing file.
    result = bpy.ops.halo.import_texture(filepath=str(png_path), target="OUTER")
    assert result == {"FINISHED"}, result
    assert primitive.halo_node.texture == first_id
    pack_root = Path(blender_scene.definition_pack_root(scene, "trinity:serina"))
    imported = list((pack_root / "assets/trinity/textures/halo").glob("serina_detail*.png"))
    assert [path.name for path in imported] == ["serina_detail.png"], imported

    # An old source filename must never leak into a new export.
    item.source_path = "assets/minecraft/halo_definitions/halo.json"
    export_root = scratch / "export"
    blender_scene.export_pack_from_scene(scene, export_root, zip_output=False, overwrite=False)
    expected = export_root / "assets/trinity/halo_definitions/serina.json"
    assert expected.is_file(), expected
    assert not (export_root / "assets/trinity/halo_definitions/halo.json").exists()
    assert json.loads(expected.read_text(encoding="utf-8"))["id"] == "trinity:serina"

    # Two different IDs may still normalize to one portable filename.
    assert bpy.ops.halo.new_definition(definition_id="trinity:path/serina") == {"FINISHED"}
    assert bpy.ops.halo.new_definition(definition_id="trinity:path_serina") == {"FINISHED"}
    validation = operators.validate_scene(scene)
    assert any("光环定义文件名冲突" in error for error in validation["errors"]), validation
    try:
        blender_scene.export_pack_from_scene(scene, scratch / "collision", zip_output=False)
    except ValueError as exc:
        assert "请先修改光环 ID" in str(exc), exc
    else:
        raise AssertionError("Definition filename collision was not rejected")
finally:
    halo_pack_editor.unregister()
    shutil.rmtree(scratch, ignore_errors=True)

print("ID_RESOURCE_OK")
