"""Blender 5.2 integration coverage for Halo 2.0 native OBJ primitives."""

from __future__ import annotations

import json
import shutil
import sys
import uuid
import zipfile
from pathlib import Path

import bpy


project_root = Path(sys.argv[sys.argv.index("--") + 1]).resolve()
halo_root = Path(sys.argv[sys.argv.index("--") + 2]).resolve()
sys.path.insert(0, str(project_root))

import halo_pack_editor
from halo_pack_editor import blender_scene, handlers, operators, panels
from halo_pack_editor.materials import mesh_mask_resolution_warning
from halo_pack_editor.obj_mesh import load_obj_resource, resolve_model_path
from halo_pack_editor.resource_store import embed_resources, ensure_resources


test_root = Path(r"F:\codex-cache\halo-blender-addon\tests") / f"native-mesh-{uuid.uuid4().hex}"
pack_root = test_root / "pack"
definition_root = pack_root / "assets" / "halo" / "halo_definitions"
model_root = pack_root / "assets" / "halo" / "models" / "halo"
texture_root = pack_root / "assets" / "halo" / "textures" / "halo"
definition_root.mkdir(parents=True)
model_root.mkdir(parents=True)
texture_root.mkdir(parents=True)
(pack_root / "pack.mcmeta").write_text(
    json.dumps({"pack": {"pack_format": 15, "description": "Native mesh test"}}),
    encoding="utf-8",
)
source_assets = halo_root / "src" / "main" / "resources" / "assets" / "halo"
for name in ("mesh_demo", "mesh_mask_demo", "mesh_step_demo", "mesh_preserve_demo", "mesh_mask_resolution_demo"):
    shutil.copy2(source_assets / "halo_definitions" / f"{name}.json", definition_root / f"{name}.json")
shutil.copy2(source_assets / "models" / "halo" / "mesh_demo.obj", model_root / "mesh_demo.obj")
shutil.copy2(source_assets / "textures" / "halo" / "mesh_demo.png", texture_root / "mesh_demo.png")
shutil.copy2(source_assets / "textures" / "halo" / "mesh_demo_mask.png", texture_root / "mesh_demo_mask.png")
shutil.copy2(source_assets / "textures" / "halo" / "mesh_demo_mask_16.png", texture_root / "mesh_demo_mask_16.png")
shutil.copy2(source_assets / "textures" / "halo" / "mesh_demo_mask_64.png", texture_root / "mesh_demo_mask_64.png")

halo_pack_editor.register()
data = blender_scene.import_project_to_scene(bpy.context, pack_root, replace=True)
assert len(data["definitions"]) == 5
scene = bpy.context.scene
meshes = [obj for obj in scene.objects if obj.get("halo_role") == "primitive" and obj.halo_node.primitive_type == "mesh"]
assert len(meshes) == 6
assert len({obj.data.materials[0].as_pointer() for obj in meshes}) == 6
for obj in (item for item in meshes if item.get("halo_definition_id") != "halo:mesh_preserve_demo"):
    assert len(obj.data.vertices) == 175
    assert len(obj.data.polygons) == 288
    dimensions = tuple(round(value, 5) for value in obj.dimensions)
    source_size = (0.8, 0.12, 0.8) if obj.get("halo_definition_id") == "halo:mesh_mask_resolution_demo" \
        else (0.9, 0.14, 0.9)
    expected_dimensions = (source_size[0], source_size[2], source_size[1])
    assert dimensions == expected_dimensions, dimensions
    assert obj.halo_node.mesh_model == "halo:models/halo/mesh_demo.obj"
    assert all(abs(actual - expected) < 1e-5 for actual, expected in zip(obj.halo_node.mesh_size, source_size))

preserve_obj = next(obj for obj in meshes if obj.get("halo_definition_id") == "halo:mesh_preserve_demo")
assert preserve_obj.halo_node.mesh_preserve_proportions
assert abs(preserve_obj.halo_node.mesh_scale - 0.4) < 1e-6
preserve_source = load_obj_resource(preserve_obj.halo_node.mesh_model, pack_root)
source_extent = tuple(preserve_source.maximum[index] - preserve_source.minimum[index] for index in range(3))
expected_dimensions = (source_extent[0] * 0.4, source_extent[2] * 0.4, source_extent[1] * 0.4)
assert all(abs(actual - expected) < 1e-5
           for actual, expected in zip(preserve_obj.dimensions, expected_dimensions))
# Property callbacks rebuild immediately in both modes. A preserve-mode JSON
# that omitted size receives the editable default size only after switching to
# the legacy fitting mode; retaining it after switching back is valid because
# Halo ignores size while preserve_proportions is true.
preserve_obj.halo_node.mesh_preserve_proportions = False
assert all(abs(actual - 1.0) < 1e-5 for actual in preserve_obj.dimensions)
preserve_obj.halo_node.mesh_preserve_proportions = True
assert all(abs(actual - expected) < 1e-5
           for actual, expected in zip(preserve_obj.dimensions, expected_dimensions))

resolution_meshes = [obj for obj in meshes if obj.get("halo_definition_id") == "halo:mesh_mask_resolution_demo"]
assert all(mesh_mask_resolution_warning(obj.data.materials[0]) is None for obj in resolution_meshes)

mask_obj = next(obj for obj in meshes if obj.get("halo_definition_id") == "halo:mesh_mask_demo")
step_obj = next(obj for obj in meshes if obj.get("halo_definition_id") == "halo:mesh_step_demo")
assert mask_obj.halo_node.mesh_mask_enabled
assert mask_obj.halo_node.mesh_mask_mode == "linear"
assert step_obj.halo_node.mesh_mask_mode == "step"
mask_material = mask_obj.data.materials[0]
assert mask_material.node_tree.nodes.get("Halo Mask Texture").interpolation == "Closest"
assert mask_material.node_tree.nodes.get("Halo Mask Texture").extension == "REPEAT"
assert mask_material.node_tree.nodes.get("Halo Mask UV Offset") is not None
assert step_obj.data.materials[0].node_tree.nodes.get("Halo Mask Step") is not None

scene.render.fps = 20
scene.frame_start = 1
scene.halo_project.preview_mode = "IDLE"
scene.frame_set(1)
handlers.update_animation(scene)
start_u = mask_material.node_tree.nodes["Halo Mask UV Offset"].inputs[1].default_value[0]
scene.frame_set(21)
handlers.update_animation(scene)
end_u = mask_material.node_tree.nodes["Halo Mask UV Offset"].inputs[1].default_value[0]
assert abs(start_u) < 1e-6 and abs(end_u - 0.125) < 1e-5, (start_u, end_u)

# The graphical term operators edit the primitive JSON, preserve existing
# fields and immediately refresh the material preview.
bpy.context.view_layer.objects.active = mask_obj
mask_obj.select_set(True)
scene.halo_project.mesh_mask_axis = "u"
before = len(panels._mesh_mask_terms_for_panel(mask_obj, "u"))
assert bpy.ops.halo.mesh_mask_term_add(function="linear", start=0.25, speed=0.5) == {"FINISHED"}
assert len(panels._mesh_mask_terms_for_panel(mask_obj, "u")) == before + 1
assert bpy.ops.halo.mesh_mask_term_edit(index=before, function="cos", amplitude=0.2, omega=2.0, phi=0.3) == {"FINISHED"}
assert panels._mesh_mask_terms_for_panel(mask_obj, "u")[before]["function"] == "cos"

# External OBJ/PNG linking uses the definition namespace, lower-cases resource
# names and validates the model before mutating the selected primitive.
external_obj = test_root / "External.OBJ"
external_mask = test_root / "External_Mask.PNG"
shutil.copy2(model_root / "mesh_demo.obj", external_obj)
generated_mask = bpy.data.images.new("External native mesh mask", width=2, height=3, alpha=True)
generated_mask.pixels = [0.2, 0.2, 0.2, 1.0, 0.4, 0.4, 0.4, 1.0,
                         0.6, 0.6, 0.6, 1.0, 0.8, 0.8, 0.8, 1.0,
                         1.0, 1.0, 1.0, 1.0, 0.0, 0.0, 0.0, 1.0]
generated_mask.filepath_raw = str(external_mask)
generated_mask.file_format = "PNG"
generated_mask.save()
bpy.data.images.remove(generated_mask)
assert bpy.ops.halo.import_mesh_model(filepath=str(external_obj)) == {"FINISHED"}
assert mask_obj.halo_node.mesh_model == "halo:models/halo/external.obj"
assert bpy.ops.halo.import_texture(filepath=str(external_mask), target="MASK") == {"FINISHED"}
assert mask_obj.halo_node.mesh_mask_texture == "halo:textures/halo/external_mask.png"
assert mesh_mask_resolution_warning(mask_obj.data.materials[0]) is not None
validation = operators.validate_scene(scene)
assert any("请调整 PNG 像素尺寸" in message for message in validation["warnings"])
assert not any("Alpha Mask 与主纹理尺寸" in message for message in validation["errors"])

# OBJ and both PNG inputs survive loss of the editable cache through the
# source archive embedded in a .blend save.
embed_resources(scene)
source_entry = next(item for item in scene.halo_project.sources if item.source_id ==
                    next(definition for definition in scene.halo_project.definitions
                         if definition.definition_id == "halo:mesh_mask_demo").source_id)
old_cache = Path(source_entry.pack_root)
shutil.rmtree(old_cache)
restore = ensure_resources(scene, restore_saved=True)
assert restore["restored_sources"] >= 1
assert resolve_model_path(mask_obj.halo_node.mesh_model, blender_scene.definition_pack_root(scene, "halo:mesh_mask_demo"))
assert mask_obj.data.materials[0].node_tree.nodes.get("Halo Mask Texture") is not None

target = Path(r"F:\codex-cache\halo-blender-addon\tests\native-mesh-export.zip")
blender_scene.export_pack_from_scene(scene, target, zip_output=True, overwrite=True)
with zipfile.ZipFile(target) as archive:
    names = set(archive.namelist())
    assert "assets/halo/models/halo/external.obj" in names
    assert "assets/halo/textures/halo/external_mask.png" in names
    payload = json.loads(archive.read("assets/halo/halo_definitions/mesh_mask_demo.json"))
    primitive = payload["layers"][0]["primitive"]
    assert primitive["type"] == "mesh"
    assert primitive["model"] == "halo:models/halo/external.obj"
    assert primitive["material"]["effects"][0]["uv_offset"]["u"][1]["function"] == "cos"
    preserve_payload = json.loads(archive.read("assets/halo/halo_definitions/mesh_preserve_demo.json"))
    preserve_primitive = preserve_payload["layers"][0]["primitive"]
    assert preserve_primitive["preserve_proportions"] is True
    assert abs(preserve_primitive["scale"] - 0.4) < 1e-6
    assert preserve_primitive["size"] == [1.0, 1.0, 1.0]

print("Halo 2.0 native mesh integration test passed")
