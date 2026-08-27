"""Verify packed Mesh-conversion textures survive a .blend reopen and export."""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path

import bpy


project_root = Path(sys.argv[sys.argv.index("--") + 1]).resolve()
cache_root = Path(r"F:\codex-cache\halo-blender-addon\tests")
sys.path.insert(0, str(project_root))

import halo_pack_editor
from halo_pack_editor import blender_scene


halo_pack_editor.register()
wrappers = [
    obj for obj in bpy.data.objects
    if obj.get("halo_role") == "group"
    and getattr(getattr(obj, "halo_node", None), "node_id", "") == "Triangle Source Mesh"
]
assert len(wrappers) == 1
wrapper = wrappers[0]
faces = [child for child in wrapper.children if child.get("halo_role") == "group"]
assert len(faces) == 2
primitives = [child for face in faces for child in face.children if child.get("halo_role") == "primitive"]
assert len(primitives) == 2
images = [image for image in bpy.data.images if image.get("halo_generated_texture")]
assert len(images) == 2
assert all(image.packed_file is not None for image in images)

target = cache_root / "mesh-conversion-reopen-export.zip"
blender_scene.export_pack_from_scene(bpy.context.scene, target, zip_output=True, overwrite=True)
with zipfile.ZipFile(target, "r") as archive:
    names = set(archive.namelist())
    for primitive in primitives:
        namespace, relative = primitive.halo_node.texture.split(":", 1)
        assert f"assets/{namespace}/{relative}" in names
target.unlink()
print("MESH_REOPEN_OK", len(faces), len(images))
