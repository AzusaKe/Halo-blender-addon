"""Regression for the real Serina resident and startup animation."""

import json
from pathlib import Path
import sys

import bpy

project_root = Path(sys.argv[sys.argv.index("--") + 1]).resolve()
pack_path = Path(sys.argv[sys.argv.index("--") + 2]).resolve()
sys.path.insert(0, str(project_root))

import halo_pack_editor
from halo_pack_editor import blender_scene, handlers

halo_pack_editor.register()
data = blender_scene.import_project_to_scene(bpy.context, pack_path, replace=True)
assert data["definitions"] and not data["definitions"][0]["error"]
scene = bpy.context.scene
project = scene.halo_project
assert project.active_definition == "trinity:serina"
group = next(obj for obj in scene.objects if obj.get("halo_role") == "group" and obj.halo_node.node_id == "serina")
primitive = next(child for child in group.children if child.get("halo_role") == "primitive")

scene.render.fps = 20
project.preview_mode = "IDLE"
scene.frame_start = 1
scene.frame_set(1)
resident_start = tuple(group.delta_location)
glow_start = float(group["halo_preview_glow"])
scene.frame_set(21)
resident_end = tuple(group.delta_location)
glow_end = float(group["halo_preview_glow"])
assert resident_start != resident_end, (resident_start, resident_end)
assert abs(resident_start[2]) < 1e-6 and abs(resident_end[2] - 0.02) < 1e-5
assert abs(glow_start - 0.75) < 1e-5 and abs(glow_end - 1.0) < 1e-5

project.preview_mode = "STARTUP"
project.preview_phase = 0.0
durations = {mode: handlers.preview_duration(scene, mode) for mode in ("STARTUP", "SHUTDOWN", "SEQUENCE")}
print("SERINA_DURATIONS", durations)
assert abs(durations["STARTUP"] - 0.6) < 1e-6
assert abs(durations["SHUTDOWN"] - 0.6) < 1e-6
assert abs(durations["SEQUENCE"] - 2.2) < 1e-6
scene.frame_set(101)
handlers.prepare_preview_playback(scene, "STARTUP")
assert scene.frame_current == 1
assert scene.frame_end == 13, scene.frame_end
startup_start = tuple(group.delta_scale)
scene.frame_set(6)
startup_middle = tuple(group.delta_scale)
assert max(abs(value) for value in startup_start) < 1e-6, startup_start
assert all(0.9 < value < 1.0 for value in startup_middle), startup_middle

# Serina has no authored shutdown; the preview must animate the reversed startup.
handlers.prepare_preview_playback(scene, "SHUTDOWN")
shutdown_start = tuple(group.delta_scale)
scene.frame_set(scene.frame_end)
shutdown_end = tuple(group.delta_scale)
assert shutdown_start != shutdown_end, (shutdown_start, shutdown_end)
assert all(abs(value - 1.0) < 1e-5 for value in shutdown_start), shutdown_start
assert max(abs(value) for value in shutdown_end) < 1e-5, shutdown_end

# The material must receive resident glow changes as well as object motion.
material = primitive.data.materials[0]
assert abs(float(material["halo_preview_glow"]) - float(group["halo_preview_glow"])) < 1e-6
print("SERINA_ANIMATION_OK", json.dumps({
    "resident_location": [resident_start, resident_end],
    "resident_glow": [glow_start, glow_end],
    "startup_scale": [startup_start, startup_middle],
    "shutdown_scale": [shutdown_start, shutdown_end],
}))
halo_pack_editor.unregister()
