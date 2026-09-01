"""Export only final JSON texture dependencies; keep source caches untouched."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import zipfile

import bpy

project_root = Path(sys.argv[sys.argv.index("--") + 1]).resolve()
sys.path.insert(0, str(project_root))
cache = Path(r"F:\codex-cache\halo-blender-addon\export-texture-tests")
cache.mkdir(parents=True, exist_ok=True)
test_root = Path(tempfile.mkdtemp(prefix="usage_", dir=cache))
os.environ["BLENDER_USER_DATAFILES"] = str(test_root / "datafiles")

import halo_pack_editor
from halo_pack_editor import blender_scene
from halo_pack_editor.materials import load_texture_image

halo_pack_editor.register()
assert bpy.ops.halo.new_project() == {"FINISHED"}
scene = bpy.context.scene
item = scene.halo_project.definitions[0]
pack_root = Path(scene.halo_project.sources[0].pack_root)
obj = next(obj for obj in scene.objects if obj.get("halo_role") == "primitive")


def png(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    image = bpy.data.images.new("export-fixture", width=2, height=2, alpha=True)
    image.pixels = [0.2, 0.6, 1.0, 1.0] * 4
    image.filepath_raw, image.file_format = str(path), "PNG"
    image.save()
    bpy.data.images.remove(image)


def family(namespace, name):
    paths = set()
    for suffix in ("", "_n", "_s", "_e"):
        relative = f"assets/{namespace}/textures/halo/{name}{suffix}.png"
        png(pack_root / relative)
        (pack_root / (relative + ".mcmeta")).write_text('{"animation":{"frametime":2}}')
        paths.update((relative, relative + ".mcmeta"))
    return paths


def packed_image(identifier):
    image = bpy.data.images.new(identifier, width=2, height=2, alpha=True)
    image.pixels = [1.0, 0.4, 0.2, 1.0] * 4
    image["halo_texture_id"] = identifier
    image["halo_generated_texture"] = True
    image.pack()
    image.use_fake_user = True
    return image


old = family("minecraft", "original")
obj.halo_node.texture = "minecraft:textures/halo/original.png"
item.definition_id = "oldname:halo"
old |= family("oldname", "intermediate")
obj.halo_node.texture = "oldname:textures/halo/intermediate.png"
item.definition_id = "newname:final"
current = family("newname", "current")
obj.halo_node.texture = "newname:textures/halo/current.png"
old |= family("deleted", "unused")
ghost = load_texture_image("deleted:textures/halo/unused.png", pack_root)
ghost.use_fake_user = True
shared, inner = family("common", "shared"), family("common", "inside")

# Another definition, hidden in the viewport, owns the Ring inside image.
source = scene.halo_project.sources[0]
blender_scene.import_definition_to_scene(scene, {
    "id": "hidden:halo", "raw": {"id": "hidden:halo", "layers": [{"id": "ring", "primitive": {
        "type": "ring", "outer_texture": "common:textures/halo/shared.png",
        "inner_texture": "common:textures/halo/inside.png", "size": [1, 0.1], "segments": 16}}]},
}, source_id=source.source_id, pack_root=str(pack_root))
scene.halo_project.definitions[1].visible = False
bpy.context.view_layer.objects.active = obj.parent
assert bpy.ops.halo.add_primitive(texture="common:textures/halo/shared.png") == {"FINISHED"}

# Packed Mesh-style images: one referenced by a live primitive, one by a raw
# extension field, and one orphan left over from the old namespace.
packed_image("mesh:textures/halo/current.png")
packed_image("future:textures/halo/template.png")
unused_generated = packed_image("minecraft:textures/halo/old_mesh.png")
bpy.context.view_layer.objects.active = obj.parent
assert bpy.ops.halo.add_primitive(texture="mesh:textures/halo/current.png") == {"FINISHED"}
root = obj.parent.parent
raw = json.loads(root["halo_raw_json"])
raw["future_field"] = {"texture": "future:textures/halo/template.png"}
root["halo_raw_json"] = json.dumps(raw)

# Old cached JSON must not keep textures alive after it is replaced at export.
stale_json = pack_root / "assets/minecraft/halo_definitions/stale.json"
stale_json.parent.mkdir(parents=True, exist_ok=True)
stale_json.write_text(json.dumps({"id": "minecraft:halo", "shape": {"texture": "minecraft:textures/halo/original.png"}}))
png(pack_root / "pack.png")
(pack_root / "notes.bin").write_bytes(b"keep unknown data\x00\xff")
png(pack_root / "assets/misc/custom/logo.png")
source_bytes = {p.relative_to(pack_root).as_posix(): p.read_bytes() for p in pack_root.rglob("*") if p.is_file()}
generated_paths = {"assets/mesh/textures/halo/current.png", "assets/future/textures/halo/template.png"}


def check_export(label, expected):
    for zipped in (False, True):
        destination = test_root / (label + (".zip" if zipped else "-folder"))
        blender_scene.export_pack_from_scene(scene, destination, zip_output=zipped)
        if zipped:
            with zipfile.ZipFile(destination) as archive:
                files = {name: archive.read(name) for name in archive.namelist()}
        else:
            files = {p.relative_to(destination).as_posix(): p.read_bytes() for p in destination.rglob("*") if p.is_file()}
            assert not (destination / "assets/minecraft").exists()
            assert not (destination / "assets/oldname").exists()
        textures = {p for p in files if "/textures/" in p and p.endswith((".png", ".png.mcmeta"))}
        assert textures == expected, (textures - expected, expected - textures)
        for relative in expected - generated_paths:
            assert files[relative] == source_bytes[relative], relative
        for path in ("pack.png", "notes.bin", "assets/misc/custom/logo.png"):
            assert files[path] == source_bytes[path]
        assert not old.intersection(files)
        assert "assets/minecraft/textures/halo/old_mesh.png" not in files
        definitions = [json.loads(data) for path, data in files.items() if "/halo_definitions/" in path and path.endswith(".json")]
        assert {d["id"] for d in definitions} == {d.definition_id for d in scene.halo_project.definitions}


check_export("renamed", current | shared | inner | generated_paths)
assert {p.relative_to(pack_root).as_posix(): p.read_bytes() for p in pack_root.rglob("*") if p.is_file()} == source_bytes
assert ghost.name in bpy.data.images and unused_generated.name in bpy.data.images

# The .blend deliberately keeps old resources for further editing, but a
# restore from its embedded source snapshot must not resurrect them in export.
bpy.ops.wm.save_as_mainfile(filepath=str(test_root / "editor.blend"))
bpy.ops.wm.open_mainfile(filepath=str(test_root / "editor.blend"))
scene = bpy.context.scene
check_export("reopened", current | shared | inner | generated_paths)
blender_scene.remove_definition_from_scene(scene, 1)
check_export("hidden-removed", current | shared | generated_paths)
blender_scene.remove_definition_from_scene(scene, 0)
check_export("all-removed", set())
print("EXPORT_TEXTURE_USAGE_OK", test_root)
halo_pack_editor.unregister()
