"""Verify managed objects and scene properties survive saving and reopening."""

from __future__ import annotations

import sys
from pathlib import Path

import bpy


project_root = Path(sys.argv[sys.argv.index("--") + 1]).resolve()
sys.path.insert(0, str(project_root))
import halo_pack_editor

halo_pack_editor.register()
roots = [obj for obj in bpy.data.objects if obj.get("halo_role") == "definition_root"]
groups = [obj for obj in bpy.data.objects if obj.get("halo_role") == "group"]
primitives = [obj for obj in bpy.data.objects if obj.get("halo_role") == "primitive"]
assert len(roots) == 1
assert len(groups) == 57
assert len(primitives) == 29
assert bpy.context.scene.halo_project.definitions[0].raw_json
print("REOPEN_OK", len(groups), len(primitives))
halo_pack_editor.unregister()
