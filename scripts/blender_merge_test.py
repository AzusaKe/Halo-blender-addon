"""Blender 5.2 integration test for multi-source merged projects."""

from __future__ import annotations

import json
import sys
import tempfile
import zipfile
from pathlib import Path

import bpy


project_root = Path(sys.argv[sys.argv.index("--") + 1]).resolve()
cache_root = Path(r"F:\codex-cache\halo-blender-addon\merge-tests")
cache_root.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(project_root))

import halo_pack_editor
from halo_pack_editor import blender_scene
from halo_pack_editor.core.pack_io import import_pack


def write_png(path: Path, rgba):
    path.parent.mkdir(parents=True, exist_ok=True)
    image = bpy.data.images.new("merge-test-png", width=2, height=2, alpha=True)
    image.pixels = list(rgba) * 4
    image.filepath_raw = str(path)
    image.file_format = "PNG"
    image.save()
    bpy.data.images.remove(image)


def definition(marker: str):
    return {
        "id": "demo:halo",
        "version": "1.0.10",
        "marker": marker,
        "layers": [{
            "id": "plate",
            "position": [0, 0, 0],
            "rotation": [0, 0, 0],
            "scale": 1,
            "primitive": {
                "type": "billboard",
                "texture": "demo:textures/halo/shared.png",
                "size": [1, 1],
            },
        }],
        "animation": {},
        "positioning": {"offset": [0, 0, 0], "scale": 1},
    }


halo_pack_editor.register()
scene = bpy.context.scene
test_root = Path(tempfile.mkdtemp(prefix="halo_merge_", dir=str(cache_root)))
folder_pack = test_root / "Folder A"
zip_tree = test_root / "Zip B"
for root, marker, color, unknown in (
    (folder_pack, "folder", (1.0, 0.0, 0.0, 1.0), "folder-only"),
    (zip_tree, "zip", (0.0, 0.0, 1.0, 1.0), "zip-only"),
):
    definition_path = root / "assets/demo/halo_definitions/halo.json"
    definition_path.parent.mkdir(parents=True, exist_ok=True)
    definition_path.write_text(json.dumps(definition(marker)), encoding="utf-8")
    write_png(root / "assets/demo/textures/halo/shared.png", color)
    write_png(root / "pack.png", color)
    (root / f"{marker}.txt").write_text(unknown, encoding="utf-8")
    (root / "pack.mcmeta").write_text(json.dumps({"pack": {"pack_format": 15, "description": "merge test"}}), encoding="utf-8")

zip_pack = test_root / "Zip B.zip"
with zipfile.ZipFile(zip_pack, "w", compression=zipfile.ZIP_DEFLATED) as archive:
    for file_path in sorted(zip_tree.rglob("*")):
        if file_path.is_file():
            archive.write(file_path, file_path.relative_to(zip_tree).as_posix())

first = blender_scene.import_project_to_scene(bpy.context, folder_pack, replace=True)
folder_source = scene.halo_project.sources[0]
assert Path(folder_source.pack_root).resolve() != folder_pack.resolve()
assert Path(folder_source.pack_root).resolve().is_relative_to(
    Path(bpy.utils.user_resource("DATAFILES", path="halo_pack_editor/cache", create=True)).resolve()
)
original_folder_texture = (folder_pack / "assets/demo/textures/halo/shared.png").read_bytes()
# Simulate a stale nested source definition not represented by the current
# scene. Export must remove it recursively before writing authoritative data.
stale_nested = Path(folder_source.pack_root) / "assets/demo/halo_definitions/archive/stale.json"
stale_nested.parent.mkdir(parents=True, exist_ok=True)
stale_nested.write_text(json.dumps({"id": "demo:stale", "layers": []}), encoding="utf-8")
scene.halo_project.manifest_json = json.dumps({"pack": {"pack_format": 15, "description": "edited aggregate"}})
second = blender_scene.import_project_to_scene(bpy.context, zip_pack)
project = scene.halo_project
assert len(project.sources) == 2
assert json.loads(project.manifest_json)["pack"]["description"] == "edited aggregate"
assert len(project.definitions) == 2
assert [item.definition_id for item in project.definitions] == ["demo:halo", "demo:halo_2"]
assert second["renamed_definitions"] == [{"from": "demo:halo", "to": "demo:halo_2"}]
assert project.definitions[1].source_path.endswith("/halo_2.json")
assert json.loads(project.definitions[1].raw_json)["id"] == "demo:halo_2"
assert json.loads(project.definitions[0].raw_json)["marker"] == "folder"
assert json.loads(project.definitions[1].raw_json)["marker"] == "zip"

# Identical resource IDs from separate sources must not reuse one Blender
# Image/Material and silently make one source preview display the other PNG.
primitive_images = {}
for obj in scene.objects:
    if obj.get("halo_role") != "primitive":
        continue
    image = next(node.image for node in obj.data.materials[0].node_tree.nodes if node.bl_idname == "ShaderNodeTexImage")
    primitive_images[obj.get("halo_definition_id")] = image
assert primitive_images["demo:halo"] != primitive_images["demo:halo_2"]
assert Path(primitive_images["demo:halo"].get("halo_source_path")).resolve().is_relative_to(Path(folder_source.pack_root).resolve())
assert not Path(primitive_images["demo:halo"].get("halo_source_path")).resolve().is_relative_to(folder_pack.resolve())
assert not Path(primitive_images["demo:halo_2"].get("halo_source_path")).resolve().is_relative_to(folder_pack.resolve())

# A saved project may outlive the operating-system ZIP extraction directory.
# Clearing the stored cache path simulates that reopen and must rebuild it
# from the untouched source ZIP, then rebind its materials.
zip_source = project.sources[1]
zip_source.pack_root = ""
assert blender_scene.ensure_source_roots(scene) == 1
assert Path(zip_source.pack_root).is_dir()
zip_primitive = next(obj for obj in scene.objects if obj.get("halo_role") == "primitive" and obj.get("halo_definition_id") == "demo:halo_2")
zip_image = next(node.image for node in zip_primitive.data.materials[0].node_tree.nodes if node.bl_idname == "ShaderNodeTexImage")
assert Path(zip_image.get("halo_source_path")).resolve().is_relative_to(Path(zip_source.pack_root).resolve())

# New definitions append too and share the same conflict policy.
assert bpy.ops.halo.new_definition(definition_id="demo:halo") == {"FINISHED"}
assert [item.definition_id for item in project.definitions] == ["demo:halo", "demo:halo_2", "demo:halo_3"]
local_source = next(source for source in project.sources if source.source_kind == "LOCAL")
assert project.definitions[2].source_id == local_source.source_id
Path(local_source.pack_root, "local-only.txt").write_text("local edit", encoding="utf-8")

# Resource-pack metadata has a dedicated editor, while unknown pack.mcmeta
# fields and an explicit cover override must survive the merged export.
manifest = json.loads(project.manifest_json)
manifest["unknown_meta"] = {"keep": True}
project.manifest_json = json.dumps(manifest, ensure_ascii=False, indent=2)
project.pack_description = "面板编辑后的描述"
assert json.loads(project.manifest_json)["pack"]["description"] == "面板编辑后的描述"
assert json.loads(project.manifest_json)["unknown_meta"] == {"keep": True}
custom_cover = test_root / "custom-cover.png"
write_png(custom_cover, (0.1, 0.8, 0.2, 1.0))
custom_cover_bytes = custom_cover.read_bytes()
assert bpy.ops.halo.import_pack_cover(filepath=str(custom_cover)) == {"FINISHED"}
assert project.pack_cover_source_id == local_source.source_id
assert Path(local_source.pack_root, *blender_scene.PROJECT_COVER_RELATIVE.parts).read_bytes() == custom_cover_bytes

# Same resource ID in another Blender scene must not be touched by editor-only
# visibility, rename, or removal in the active project scene.
other_scene = bpy.data.scenes.new("Merge isolation scene")
other_object = bpy.data.objects.new("Other scene same-ID object", None)
other_object["halo_definition_id"] = "demo:halo"
other_scene.collection.objects.link(other_object)

# Visibility is editor-only and affects exactly one definition tree.
first_item = project.definitions[0]
second_item = project.definitions[1]
first_item.visible = False
first_objects = [obj for obj in scene.objects if obj.get("halo_definition_id") == first_item.definition_id]
second_objects = [obj for obj in scene.objects if obj.get("halo_definition_id") == second_item.definition_id]
assert first_objects and all(obj.hide_viewport and obj.hide_render for obj in first_objects)
assert second_objects and all(not obj.hide_viewport and not obj.hide_render for obj in second_objects)
assert not other_object.hide_viewport and not other_object.hide_render
first_item.visible = True
assert all(not obj.hide_viewport and not obj.hide_render for obj in first_objects)

merged_zip = test_root / "merged.zip"
blender_scene.export_pack_from_scene(scene, merged_zip, zip_output=True)
merged = import_pack(merged_zip)
assert {item.identifier for item in merged.definitions} == {"demo:halo", "demo:halo_2", "demo:halo_3"}
assert merged.pack_mcmeta["pack"]["description"] == "面板编辑后的描述"
assert merged.pack_mcmeta["unknown_meta"] == {"keep": True}
assert merged.pack_mcmeta["pack"]["supported_formats"] == {
    "min_inclusive": 15,
    "max_inclusive": 2_147_483_647,
}
assert merged.pack_mcmeta["pack"]["min_format"] == [15, 0]
assert merged.pack_mcmeta["pack"]["max_format"] == 2_147_483_647
assert "folder.txt" in merged.files and "zip.txt" in merged.files
assert "local-only.txt" in merged.files
assert all("visible" not in item.document.data for item in merged.definitions)
with zipfile.ZipFile(merged_zip) as archive:
    definition_files = [name for name in archive.namelist() if "/halo_definitions/" in name and name.endswith(".json")]
    assert archive.read("pack.png") == custom_cover_bytes
    assert not any(name.startswith(".halo_pack_editor/") for name in archive.namelist())
assert len(definition_files) == 3

assert bpy.ops.halo.clear_pack_cover() == {"FINISHED"}
restored_cover_zip = test_root / "restored-cover.zip"
blender_scene.export_pack_from_scene(scene, restored_cover_zip, zip_output=True)
with zipfile.ZipFile(restored_cover_zip) as archive:
    assert archive.read("pack.png") == (zip_tree / "pack.png").read_bytes()
    assert not any(name.startswith(".halo_pack_editor/") for name in archive.namelist())

# Removing one local definition must not let a copied source JSON resurrect it.
removed = blender_scene.remove_definition_from_scene(scene, 2)
assert removed == "demo:halo_3"
without_local = test_root / "without-local.zip"
blender_scene.export_pack_from_scene(scene, without_local, zip_output=True)
assert {item.identifier for item in import_pack(without_local).definitions} == {"demo:halo", "demo:halo_2"}

# Removing Folder A also removes only its bound definition and resources; the
# ZIP source and its auto-renamed definition remain exportable.
source_name, removed_ids = blender_scene.remove_source_from_scene(scene, 0)
assert source_name == "Folder A"
assert removed_ids == ["demo:halo"]
assert len(project.sources) == 2
assert [item.definition_id for item in project.definitions] == ["demo:halo_2"]
assert other_object.name in bpy.data.objects
without_folder = test_root / "without-folder.zip"
blender_scene.export_pack_from_scene(scene, without_folder, zip_output=True)
remaining = import_pack(without_folder)
assert [item.identifier for item in remaining.definitions] == ["demo:halo_2"]
assert "folder.txt" not in remaining.files and "zip.txt" in remaining.files
assert (folder_pack / "assets/demo/textures/halo/shared.png").read_bytes() == original_folder_texture

# A blank authored project owns an independent persistent working source. New
# definitions must bind to it instead of whichever imported pack was latest.
author_scene = bpy.data.scenes.new("Local authoring scene")
bpy.context.window.scene = author_scene
assert bpy.ops.halo.new_project(with_default_halo=True) == {"FINISHED"}
author_project = author_scene.halo_project
assert len(author_project.sources) == 1
assert author_project.sources[0].source_kind == "LOCAL"
assert author_project.definitions[0].source_id == author_project.sources[0].source_id
assert Path(author_project.sources[0].pack_root).is_dir()
assert Path(author_project.sources[0].pack_root).resolve().is_relative_to(
    Path(bpy.utils.user_resource("DATAFILES", path="halo_pack_editor/cache", create=True)).resolve()
)

print("MERGE_PROJECT_OK", merged_zip, len(merged.definitions), len(project.sources), len(project.definitions))
halo_pack_editor.unregister()
