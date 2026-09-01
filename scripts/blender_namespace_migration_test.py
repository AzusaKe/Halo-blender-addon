"""Definition rename relocates live textures, shared families and packed bakes."""

import json
import importlib
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch
import zipfile

import bpy

project_root = Path(sys.argv[sys.argv.index("--") + 1]).resolve()
sys.path.insert(0, str(project_root))
cache = Path(r"F:\codex-cache\halo-blender-addon\namespace-tests")
cache.mkdir(parents=True, exist_ok=True)
test_root = Path(tempfile.mkdtemp(prefix="rename_", dir=cache))
os.environ["BLENDER_USER_DATAFILES"] = str(test_root / "datafiles")

arguments = sys.argv[sys.argv.index("--") + 1:]
module_name = arguments[1] if len(arguments) > 1 else "halo_pack_editor"
halo_pack_editor = importlib.import_module(module_name)
blender_scene = importlib.import_module(module_name + ".blender_scene")
referenced_texture_ids = importlib.import_module(module_name + ".core.texture_usage").referenced_texture_ids

if not hasattr(bpy.types.Scene, "halo_project"):
    halo_pack_editor.register()
assert bpy.ops.halo.new_project() == {"FINISHED"}
scene = bpy.context.scene
item = scene.halo_project.definitions[0]
# A blank project's absent example image must not block namespace editing.
item.definition_id = "a:b"
assert item.definition_id == "a:b", scene.halo_project.get("halo_definition_id_error")
source = scene.halo_project.sources[0]
pack_root = Path(source.pack_root)
primitive = next(obj for obj in scene.objects if obj.get("halo_role") == "primitive")
root = next(obj for obj in scene.objects if obj.get("halo_role") == "definition_root")


def png(path, color=(0.2, 0.6, 0.9, 1.0)):
    path.parent.mkdir(parents=True, exist_ok=True)
    image = bpy.data.images.new("namespace-fixture", width=2, height=2, alpha=True)
    image.pixels = list(color) * 4
    image.filepath_raw, image.file_format = str(path), "PNG"
    image.save()
    bpy.data.images.remove(image)


for suffix in ("", "_n", "_s", "_e"):
    png(pack_root / f"assets/a/textures/halo/c{suffix}.png")
    (pack_root / f"assets/a/textures/halo/c{suffix}.png.mcmeta").write_text('{"animation":{"frametime":2}}')
png(pack_root / "assets/shared/textures/inside.png", (0.9, 0.2, 0.1, 1.0))
png(pack_root / "assets/a/textures/future.png")
# Real conflict must preserve the target image and suffix the migrating one.
png(pack_root / "assets/d/textures/halo/c.png", (0.1, 0.9, 0.2, 1.0))
target_before = (pack_root / "assets/d/textures/halo/c.png").read_bytes()
primitive.halo_node.texture = "a:textures/halo/c.png"
primitive.halo_node.primitive_type = "ring"
primitive.halo_node.inner_texture = "shared:textures/inside.png"

bake = bpy.data.images.new("packed-only-bake", width=2, height=2, alpha=True)
bake.pixels = [0.8, 0.3, 0.2, 1.0] * 4
bake["halo_texture_id"], bake["halo_generated_texture"] = "a:textures/bake.png", True
bake.use_fake_user = True
bake.pack()
bake_bytes = bytes(bake.packed_file.data)
bpy.context.view_layer.objects.active = primitive.parent
assert bpy.ops.halo.add_primitive(texture="a:textures/bake.png") == {"FINISHED"}
raw = json.loads(root["halo_raw_json"])
raw["future"] = {"texture": "a:textures/future.png", "note": "a:textures/future.png"}
root["halo_raw_json"] = json.dumps(raw)

# A different (hidden) definition must keep its shared old IDs and Images.
blender_scene.import_definition_to_scene(scene, {"id": "other:halo", "raw": {
    "id": "other:halo", "layers": [{"primitive": {"type": "ring",
        "outer_texture": "a:textures/halo/c.png", "inner_texture": "a:textures/bake.png"}}]}},
    source_id=source.source_id, pack_root=str(pack_root))
other = scene.halo_project.definitions[1]
other.visible = False
other_before = blender_scene.sync_definition_from_scene(scene, other.definition_id)

# A failed copy rolls the ID back without partially rewriting references.
with patch(module_name + ".core.texture_migration.copy_texture_with_sidecars", side_effect=OSError("test copy failure")):
    item.definition_id = "failed:b"
assert item.definition_id == "a:b"
assert primitive.halo_node.texture == "a:textures/halo/c.png"
assert "test copy failure" in scene.halo_project["halo_definition_id_error"]

item.definition_id = "d:b"
assert item.definition_id == "d:b", scene.halo_project.get("halo_definition_id_error")
assert not scene.halo_project.get("halo_definition_id_error"), scene.halo_project.get("halo_definition_id_error")
assert primitive.halo_node.texture == "d:textures/halo/c_1.png"
assert primitive.halo_node.inner_texture == "d:textures/inside.png"
assert (pack_root / "assets/d/textures/halo/c.png").read_bytes() == target_before
for suffix in ("", "_n", "_s", "_e"):
    for extension in (".png", ".png.mcmeta"):
        assert (pack_root / f"assets/d/textures/halo/c_1{suffix}{extension}").read_bytes() == (
            pack_root / f"assets/a/textures/halo/c{suffix}{extension}").read_bytes()
assert (pack_root / "assets/d/textures/bake.png").read_bytes() == bake_bytes
assert bake["halo_texture_id"] == "a:textures/bake.png"
assert bytes(bake.packed_file.data) == bake_bytes
assert blender_scene.sync_definition_from_scene(scene, other.definition_id) == other_before
assert json.loads(root["halo_raw_json"])["future"]["note"] == "a:textures/future.png"
assert all(ref.startswith("d:") for ref in referenced_texture_ids([json.loads(item.raw_json)]))
images = [node.image for mat in primitive.data.materials for node in mat.node_tree.nodes if node.type == "TEX_IMAGE"]
assert {image.get("halo_texture_id") for image in images} == {"d:textures/halo/c_1.png", "d:textures/inside.png"}
assert all(image.packed_file and not image.get("halo_missing_texture") for image in images)

# Repeated renames relocate every texture, while same-namespace ID edits do not.
item.definition_id = "e:new"
assert all(ref.startswith("e:") for ref in referenced_texture_ids([json.loads(item.raw_json)]))
refs = referenced_texture_ids([json.loads(item.raw_json)])
item.definition_id = "e:renamed"
assert referenced_texture_ids([json.loads(item.raw_json)]) == refs
item.definition_id = "other:halo"
assert item.definition_id == "e:renamed"
item.definition_id = "../escape:bad"
assert item.definition_id == "e:renamed"

def check_exports(label):
    for zipped in (False, True):
        target = test_root / (label + (".zip" if zipped else "-folder"))
        blender_scene.export_pack_from_scene(scene, target, zip_output=zipped)
        if zipped:
            with zipfile.ZipFile(target) as archive:
                files = {name: archive.read(name) for name in archive.namelist()}
        else:
            files = {p.relative_to(target).as_posix(): p.read_bytes() for p in target.rglob("*") if p.is_file()}
        assert files["assets/e/textures/bake.png"] == bake_bytes
        assert files["assets/a/textures/halo/c.png"] == (pack_root / "assets/a/textures/halo/c.png").read_bytes()
        assert not any(name.startswith(("assets/d/", "assets/minecraft/", "assets/shared/")) for name in files)
        exported = next(json.loads(data) for path, data in files.items()
                        if "/halo_definitions/" in path and json.loads(data)["id"] == "e:renamed")
        assert referenced_texture_ids([exported]) == refs

check_exports("export")
bpy.ops.wm.save_as_mainfile(filepath=str(test_root / "renamed.blend"))
bpy.ops.wm.open_mainfile(filepath=str(test_root / "renamed.blend"))
scene = bpy.context.scene
check_exports("reopened")
print("NAMESPACE_MIGRATION_OK", test_root)
halo_pack_editor.unregister()
