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

    # A sidecar belongs to the albedo resource, not only to the primitive that
    # happened to import it.  Create a second group using the same albedo.
    definition_root = primitive.parent.parent
    operators._select_object(bpy.context, definition_root)
    assert bpy.ops.halo.add_group(group_id="shared_albedo_group") == {"FINISHED"}
    shared_group = bpy.context.object
    assert bpy.ops.halo.add_primitive(texture=first_id) == {"FINISHED"}
    shared_primitive = bpy.context.object
    assert primitive.parent.halo_node.glowing is True
    assert shared_group.halo_node.glowing is True
    operators._select_object(bpy.context, primitive)

    # labPBR maps derive their identity from the selected albedo, drive the
    # integrated node preview, and disable the owning group's full-bright flag.
    normal_path = scratch / "picked_normal.png"
    specular_path = scratch / "picked_specular.png"
    for image_path, color in (
        (normal_path, [0.5, 0.5, 1.0, 1.0]),
        (specular_path, [0.5, 0.04, 0.0, 1.0]),
    ):
        test_image = bpy.data.images.new(image_path.stem, width=2, height=2, alpha=True)
        test_image.pixels[:] = color * 4
        test_image.filepath_raw = str(image_path)
        test_image.file_format = "PNG"
        test_image.save()
        bpy.data.images.remove(test_image)
    primitive.parent.halo_node.glowing = True
    assert bpy.ops.halo.import_texture(filepath=str(normal_path), target="NORMAL") == {"FINISHED"}
    assert bpy.ops.halo.import_texture(filepath=str(specular_path), target="SPECULAR") == {"FINISHED"}
    assert primitive.parent.halo_node.glowing is False
    assert shared_group.halo_node.glowing is False
    assert shared_primitive.data.materials[0].node_tree.nodes.get("Halo labPBR Surface") is not None
    normal_resource = pack_root / "assets/trinity/textures/halo/serina_detail_n.png"
    specular_resource = pack_root / "assets/trinity/textures/halo/serina_detail_s.png"
    assert normal_resource.is_file(), normal_resource
    assert specular_resource.is_file(), specular_resource
    material = primitive.data.materials[0]
    surface_node = material.node_tree.nodes.get("Halo labPBR Surface")
    assert surface_node is not None
    assert surface_node.node_tree.get("labpbr_kind") == "Surface"
    assert surface_node.node_tree.get("labpbr_complete") is True
    assert surface_node.outputs["Shader"].is_linked
    assert surface_node.inputs["Albedo Color"].is_linked
    assert surface_node.inputs["Albedo Alpha"].is_linked
    assert surface_node.inputs["Normal Color"].is_linked
    assert surface_node.inputs["Specular Color"].is_linked
    normal_node = material.node_tree.nodes.get("Halo Normal Texture")
    specular_node = material.node_tree.nodes.get("Halo Specular Texture")
    assert normal_node is not None and normal_node.image.colorspace_settings.name == "Non-Color"
    assert specular_node is not None and specular_node.image.colorspace_settings.name == "Non-Color"
    assert specular_node.interpolation == "Closest"

    replacement_path = scratch / "serina_replacement.png"
    replacement_image = bpy.data.images.new("Replacement Albedo", width=2, height=2, alpha=True)
    replacement_image.pixels[:] = [0.0, 1.0, 0.0, 1.0] * 4
    replacement_image.filepath_raw = str(replacement_path)
    replacement_image.file_format = "PNG"
    replacement_image.save()
    bpy.data.images.remove(replacement_image)
    assert bpy.ops.halo.import_texture(filepath=str(replacement_path), target="OUTER") == {"FINISHED"}
    assert primitive.halo_node.texture == "trinity:textures/halo/serina_replacement.png"
    assert (pack_root / "assets/trinity/textures/halo/serina_replacement_n.png").is_file()
    assert (pack_root / "assets/trinity/textures/halo/serina_replacement_s.png").is_file()
    assert primitive.data.materials[0].node_tree.nodes.get("Halo labPBR Surface") is not None

    # An old source filename must never leak into a new export.
    item.source_path = "assets/minecraft/halo_definitions/halo.json"
    export_root = scratch / "export"
    blender_scene.export_pack_from_scene(scene, export_root, zip_output=False, overwrite=False)
    expected = export_root / "assets/trinity/halo_definitions/serina.json"
    assert expected.is_file(), expected
    assert not (export_root / "assets/trinity/halo_definitions/halo.json").exists()
    exported_document = json.loads(expected.read_text(encoding="utf-8"))
    assert exported_document["id"] == "trinity:serina"

    def mappings(value):
        if isinstance(value, dict):
            yield value
            for child in value.values():
                yield from mappings(child)
        elif isinstance(value, list):
            for child in value:
                yield from mappings(child)

    pbr_group = next((value for value in mappings(exported_document)
                      if value.get("glowing") is False
                      and "trinity:textures/halo/serina_replacement.png" in str(value)), None)
    assert pbr_group is not None, exported_document

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
