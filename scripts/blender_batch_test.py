"""Batch import/export acceptance test for the merged 60-definition pack."""

from __future__ import annotations

import sys
from pathlib import Path

import bpy


project_root = Path(sys.argv[sys.argv.index("--") + 1]).resolve()
sys.path.insert(0, str(project_root))
import halo_pack_editor
from halo_pack_editor import blender_scene
from halo_pack_editor.core.pack_io import import_pack

halo_pack_editor.register()
source = Path(r"F:\HaloPackTool\output\All the halo pack.zip")
data = blender_scene.import_project_to_scene(bpy.context, source, replace=True)
assert len(data["definitions"]) == 60
output = Path(r"F:\codex-cache\halo-blender-addon\tests\all-pack-roundtrip.zip")
blender_scene.export_pack_from_scene(bpy.context.scene, output, zip_output=True, overwrite=True)
roundtrip = import_pack(output)
assert len(roundtrip.definitions) == 60
print("BLENDER_BATCH_OK", len(data["definitions"]), len(roundtrip.files), len(roundtrip.diagnostics))
halo_pack_editor.unregister()
