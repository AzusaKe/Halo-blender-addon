"""Run prepare/reopen in separate Blender processes; never touch user files."""

from __future__ import annotations

import io
import json
import os
import sys
import zipfile
from pathlib import Path

import bpy

args = sys.argv[sys.argv.index("--") + 1:]
project_root, test_root, mode = Path(args[0]).resolve(), Path(args[1]).resolve(), args[2]
test_root.mkdir(parents=True, exist_ok=True)
os.environ["BLENDER_USER_DATAFILES"] = str(test_root / "datafiles")
sys.path.insert(0, str(project_root))

import halo_pack_editor
from halo_pack_editor import blender_scene, handlers, operators, resource_store


def write_png(path, rgba):
    path.parent.mkdir(parents=True, exist_ok=True)
    image = bpy.data.images.new("fixture-png", width=2, height=2, alpha=True)
    image.pixels = list(rgba) * 4
    image.filepath_raw, image.file_format = str(path), "PNG"
    image.save()
    bpy.data.images.remove(image)


def fixture_pack(root, marker, color, *, ring=False):
    definition = {
        "id": "demo:" + marker, "version": "1.0.10",
        "layers": [{"id": "part", "position": [0.2, 0.4, 0.6], "rotation": [10, 20, 30],
                    "primitive": {"type": "ring" if ring else "billboard", "size": [1, 0.5],
                                  ("outer_texture" if ring else "texture"): "demo:textures/halo/shared.png",
                                  **({"inner_texture": "demo:textures/halo/inner.png", "segments": 16} if ring else {})}}],
    }
    path = root / "assets/demo/halo_definitions/halo.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(definition), encoding="utf-8")
    write_png(root / "assets/demo/textures/halo/shared.png", color)
    if ring:
        write_png(root / "assets/demo/textures/halo/inner.png", (0, 1, 0, 1))
    for suffix in ("_n", "_s", "_e"):
        write_png(root / f"assets/demo/textures/halo/shared{suffix}.png", (0.5, 0.5, 1, 1))
    (root / "assets/demo/textures/halo/shared.png.mcmeta").write_text('{"animation":{"frametime":2}}')
    (root / "unknown.bin").write_bytes(marker.encode() + b"\x00\xff")
    write_png(root / "pack.png", (1, 1, 0, 1))
    (root / "pack.mcmeta").write_text('{"pack":{"pack_format":15,"description":"persistence"}}')


def pack_zip(root, target):
    with zipfile.ZipFile(target, "w") as archive:
        for path in root.rglob("*"):
            if path.is_file():
                archive.write(path, path.relative_to(root).as_posix())


def move_offline(path):
    # Precisely validated, recoverable rename of fixtures only.
    path = Path(path).resolve()
    assert path != test_root and path.is_relative_to(test_root), path
    path.rename(path.with_name(path.name + ".offline"))


def source_files(source):
    root = Path(source.pack_root)
    return {p.relative_to(root).as_posix(): p.read_bytes().hex() for p in root.rglob("*") if p.is_file()}


def primitive(scene, definition_id):
    return next(obj for obj in scene.objects if obj.get("halo_role") == "primitive"
                and obj.get("halo_definition_id") == definition_id)


def node_images(obj):
    return [n.image for n in obj.data.materials[0].node_tree.nodes if n.type == "TEX_IMAGE" and n.image]


if mode == "prepare":
    halo_pack_editor.register()
    assert handlers.halo_save_pre in bpy.app.handlers.save_pre
    folder, tree = test_root / "folder", test_root / "zip-tree"
    fixture_pack(folder, "folder", (1, 0, 0, 1), ring=True)
    fixture_pack(tree, "zip", (0, 0, 1, 1))
    zip_path = test_root / "source.zip"
    pack_zip(tree, zip_path)
    scene = bpy.context.scene
    blender_scene.import_project_to_scene(bpy.context, folder, replace=True)
    blender_scene.import_project_to_scene(bpy.context, zip_path)
    assert bpy.ops.halo.new_definition(definition_id="demo:local") == {"FINISHED"}
    assert bpy.ops.halo.add_group() == {"FINISHED"}
    assert bpy.ops.halo.add_primitive() == {"FINISHED"}
    obj = primitive(scene, "demo:local")
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    external = test_root / "png-input" / "local.png"
    write_png(external, (1, 0, 1, 1))
    write_png(external.with_name("local_n.png"), (0, 1, 1, 1))
    assert bpy.ops.halo.import_texture(filepath=str(external)) == {"FINISHED"}
    local_id, local_image = obj.halo_node.texture, node_images(obj)[0]
    # Repeated import into another primitive, and both sides of a Ring, must
    # reuse the same resource ID / Image rather than create filename suffixes.
    bpy.context.view_layer.objects.active = obj.parent
    assert bpy.ops.halo.add_primitive(primitive_type="ring", texture=local_id) == {"FINISHED"}
    local_ring = bpy.context.object
    image_count = len(bpy.data.images)
    assert bpy.ops.halo.import_texture(filepath=str(external)) == {"FINISHED"}
    assert bpy.ops.halo.import_texture(filepath=str(external), target="INNER") == {"FINISHED"}
    assert local_ring.halo_node.texture == local_ring.halo_node.inner_texture == local_id
    assert len(bpy.data.images) == image_count
    assert all(image == local_image for image in node_images(local_ring))
    renamed = external.with_name("renamed.png")
    renamed.write_bytes(external.read_bytes())
    renamed.with_name("renamed_n.png").write_bytes(external.with_name("local_n.png").read_bytes())
    assert bpy.ops.halo.import_texture(filepath=str(renamed), target="INNER") == {"FINISHED"}
    assert local_ring.halo_node.inner_texture == local_id
    assert all(image.packed_file for obj in scene.objects if obj.get("halo_role") == "primitive"
               for image in node_images(obj))
    # The add-on must not pack arbitrary images used by the rest of a scene.
    unrelated = bpy.data.images.load(str(external), check_existing=False)
    unrelated.name, unrelated.use_fake_user = "unrelated-image", True
    expected = {s.source_id: source_files(s) for s in scene.halo_project.sources}
    scene["expected_files"] = json.dumps(expected)
    scene["local_texture_id"] = obj.halo_node.texture
    saved = test_root / "portable.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(saved))
    # Save makes Image paths Blender-relative; deduplication must also reuse
    # that Image datablock when the same PNG is linked again in this session.
    image_count = len(bpy.data.images)
    assert bpy.ops.halo.import_texture(filepath=str(external)) == {"FINISHED"}
    assert len(bpy.data.images) == image_count
    assert node_images(local_ring)[0] == local_image
    assert not unrelated.packed_file
    assert json.loads(scene.halo_project.get("halo_resource_warnings", "[]")) == []
    for source in scene.halo_project.sources:
        data = resource_store._archive(source)
        assert data
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            assert {name: archive.read(name).hex() for name in archive.namelist()} == expected[source.source_id]
    digests = [bpy.data.texts[s["halo_resource_archive"]]["halo_archive_sha256"] for s in scene.halo_project.sources]
    resource_store.embed_resources(scene)
    assert digests == [bpy.data.texts[s["halo_resource_archive"]]["halo_archive_sha256"] for s in scene.halo_project.sources]
    for source in scene.halo_project.sources:
        move_offline(source.pack_root)
    move_offline(folder)
    move_offline(zip_path)
    # Keep unrelated-image's own path valid; its resources are not ours.
    print("RESOURCE_PREPARE_OK", saved)

elif mode == "reopen":
    # Initially open with no extension: the visible Image data must already be
    # self-contained before any load handler can rescue it from disk.
    bpy.ops.wm.open_mainfile(filepath=str(test_root / "portable.blend"))
    ordinary = [i for i in bpy.data.images if i.get("halo_texture_id") and not i.get("halo_missing_texture")]
    assert len(ordinary) >= 4
    assert all(i.packed_file and len(i.pixels) == 16 and not Path(bpy.path.abspath(i.filepath)).is_file() for i in ordinary)
    assert not bpy.data.images["unrelated-image"].packed_file
    halo_pack_editor.register()
    scene = bpy.context.scene
    expected = json.loads(scene["expected_files"])
    assert json.loads(scene.halo_project.get("halo_resource_warnings", "[]")) == []
    assert len(scene.halo_project.sources) == 3
    assert all(Path(bpy.path.abspath(p.filepath)).is_file() for image in ordinary for p in image.packed_files)
    for source in scene.halo_project.sources:
        assert source_files(source) == expected[source.source_id]
    folder_images = node_images(primitive(scene, "demo:folder"))
    zip_images = node_images(primitive(scene, "demo:zip"))
    assert len(folder_images) == 2 and len(zip_images) == 1
    assert folder_images[0] != zip_images[0]
    assert folder_images[0].pixels[0] > 0.99 and zip_images[0].pixels[2] > 0.99
    assert folder_images[1].pixels[1] > 0.99
    for obj in scene.objects:
        if obj.get("halo_role") == "primitive":
            operators._set_node_mesh(obj)
            assert all(i.packed_file and not i.get("halo_missing_texture") for i in node_images(obj))
    # Partial deletion while Blender stays open: image and unused sidecars.
    root = Path(scene.halo_project.sources[0].pack_root)
    move_offline(root / "assets/demo/textures/halo/shared.png")
    move_offline(root / "assets/demo/textures/halo/shared_n.png")
    report = resource_store.ensure_resources(scene)
    assert not report["warnings"], report
    for relative, data in expected[scene.halo_project.sources[0].source_id].items():
        assert (root / relative).read_bytes().hex() == data
    assert bpy.ops.file.pack_all() == {"FINISHED"}
    assert bpy.ops.halo.pack_resources() == {"FINISHED"}
    for zipped in (False, True):
        destination = test_root / ("export.zip" if zipped else "export-folder")
        blender_scene.export_pack_from_scene(scene, destination, zip_output=zipped)
        merged = {}
        for source in scene.halo_project.sources:
            merged.update(expected[source.source_id])
        for relative, data in merged.items():
            if relative == "pack.mcmeta" or "/halo_definitions/" in relative:
                continue
            if zipped:
                with zipfile.ZipFile(destination) as archive:
                    actual = archive.read(relative)
            else:
                actual = (destination / relative).read_bytes()
            assert actual.hex() == data, relative
    bpy.ops.wm.save_as_mainfile(filepath=str(test_root / "resaved.blend"))
    # Exercise load_post as well as enabling the extension after file load.
    bpy.ops.wm.open_mainfile(filepath=str(test_root / "resaved.blend"))
    assert not json.loads(bpy.context.scene.halo_project.get("halo_resource_warnings", "[]"))
    halo_pack_editor.unregister()
    assert handlers.halo_save_pre not in bpy.app.handlers.save_pre
    print("RESOURCE_REOPEN_OK images+inner+sidecars+unknown+export+pack_all+load_post")

elif mode == "legacy_prepare":
    halo_pack_editor.register()
    scene = bpy.context.scene
    folder, zip_path = test_root / "legacy-source", test_root / "legacy.zip"
    fixture_pack(folder, "legacy", (1, 0, 0, 1), ring=True)
    pack_zip(folder, zip_path)
    blender_scene.import_project_to_scene(bpy.context, zip_path, replace=True)
    old_cache = Path(scene.halo_project.pack_root)
    lost_root = test_root / "old-temp" / "halo_pack_edit_lost"
    obj = primitive(scene, "demo:legacy")
    orphan = bpy.data.images.load(str(old_cache / "assets/demo/textures/halo/shared.png"), check_existing=False)
    orphan.use_fake_user = True
    orphan["halo_texture_id"] = "demo:textures/halo/shared.png"
    for image in [*node_images(obj), orphan]:
        relative = Path(image.filepath).relative_to(old_cache)
        if image.packed_file:
            image.unpack(method="REMOVE")
        image.filepath = str(lost_root / relative)
        image["halo_source_path"] = image.filepath
    scene.halo_project.pack_root = str(lost_root)
    scene.halo_project.sources.clear()
    scene.halo_project.definitions[0].source_id = ""
    root = obj.parent.parent
    root["halo_pack_root"], root["halo_source_id"] = str(lost_root), ""
    halo_pack_editor.unregister()  # emulate pre-fix save with no packing hook
    bpy.ops.wm.save_as_mainfile(filepath=str(test_root / "legacy.blend"))
    move_offline(old_cache)
    print("RESOURCE_LEGACY_PREPARE_OK")

elif mode == "legacy_reopen":
    bpy.ops.wm.open_mainfile(filepath=str(test_root / "legacy.blend"))
    old_images = [i for i in bpy.data.images if i.get("halo_texture_id")]
    assert old_images and all(not i.packed_file and not Path(i.filepath).exists() for i in old_images)
    halo_pack_editor.register()
    scene = bpy.context.scene
    assert len(scene.halo_project.sources) == 1
    assert all(i.packed_file and Path(i.filepath).is_file() for i in old_images)
    assert all(not i.get("halo_missing_texture") for i in node_images(primitive(scene, "demo:legacy")))
    assert not json.loads(scene.halo_project.get("halo_resource_warnings", "[]"))
    assert bpy.ops.file.pack_all() == {"FINISHED"}
    assert bpy.ops.halo.pack_resources() == {"FINISHED"}
    bpy.ops.wm.save_as_mainfile(filepath=str(test_root / "legacy-repaired.blend"))
    # A damaged embedded archive is diagnosed and never overwritten on save.
    source = scene.halo_project.sources[0]
    text = bpy.data.texts[source["halo_resource_archive"]]
    original = text.as_string()
    text.clear()
    text.write("broken backup")
    assert resource_store.ensure_resources(scene)["warnings"]
    assert resource_store.embed_resources(scene)["warnings"]
    assert text.as_string() == "broken backup"
    text.clear()
    text.write(original)
    assert not resource_store.ensure_resources(scene)["warnings"]
    # The same safe extraction is used for external and embedded archives.
    unsafe = io.BytesIO()
    with zipfile.ZipFile(unsafe, "w") as archive:
        archive.writestr("../must-not-exist.txt", "unsafe")
    unsafe.seek(0)
    try:
        blender_scene._safe_extract(unsafe, str(test_root / "safe-extraction"))
        raise AssertionError("embedded ZIP traversal was accepted")
    except ValueError:
        assert not (test_root / "must-not-exist.txt").exists()
    # A genuinely missing legacy PNG must be reported, not exported as the
    # magenta placeholder or mistakenly resolved from a different source.
    bpy.context.view_layer.objects.active = primitive(scene, "demo:legacy")
    bpy.context.object.halo_node.inner_texture = "demo:textures/halo/unrecoverable.png"
    report = resource_store.ensure_resources(scene)
    assert any("unrecoverable.png" in warning for warning in report["warnings"])
    assert not (Path(source.pack_root) / "assets/demo/textures/halo/unrecoverable.png").exists()
    print("RESOURCE_LEGACY_REOPEN_OK source+ring_inner+unused_datablock+migration")

elif mode == "cleanup_prepare":
    halo_pack_editor.register()
    scene = bpy.context.scene
    folder, zip_path = test_root / "cleanup-source", test_root / "cleanup.zip"
    fixture_pack(folder, "cleanup", (1, 0, 0, 1), ring=True)
    pack_zip(folder, zip_path)
    blender_scene.import_project_to_scene(bpy.context, zip_path, replace=True)
    obj = primitive(scene, "demo:cleanup")
    source = scene.halo_project.sources[0]
    old_root = Path(source.pack_root)
    lost_root = test_root / "Temp" / "halo_pack_edit_missing"
    for image in node_images(obj):
        relative = Path(image.filepath).relative_to(old_root)
        image.unpack(method="REMOVE")
        image.filepath_raw = str(lost_root / relative)
        image["halo_source_path"] = image.filepath
    source.pack_root = str(lost_root)
    scene.halo_project.pack_root = str(lost_root)
    obj.parent.parent["halo_pack_root"] = str(lost_root)

    def ghost(name, path, *, packed=False):
        image = bpy.data.images.load(str(folder / "assets/demo/textures/halo/shared.png"), check_existing=False)
        image.name, image.use_fake_user = name, True
        image["halo_texture_id"] = f"demo:textures/halo/{path.name}"
        if packed:
            image.pack()
        image.filepath_raw = str(path)
        image["halo_source_path"] = str(path)
        return image

    dead_orphan = ghost("dead-unused-temp", lost_root / "assets/demo/textures/halo/gone-orphan.png")
    dead_used = ghost("dead-used-temp", lost_root / "assets/demo/textures/halo/gone.png")
    bpy.context.view_layer.objects.active = obj.parent
    assert bpy.ops.halo.add_primitive(texture="demo:textures/halo/gone.png") == {"FINISHED"}
    broken = bpy.context.object
    next(n for n in broken.data.materials[0].node_tree.nodes if n.type == "TEX_IMAGE").image = dead_used
    protected_packed = ghost("packed-temp", lost_root / "assets/demo/textures/halo/packed.png", packed=True)
    unrelated = ghost("non-halo-temp", test_root / "Temp/user-missing.png")
    unrelated.pop("halo_texture_id", None)
    non_temp = ghost("halo-non-temp", test_root / "user-assets/missing.png")
    scene["cleanup_remove"] = json.dumps([dead_orphan.name, dead_used.name])
    scene["cleanup_protect"] = json.dumps([protected_packed.name, unrelated.name, non_temp.name])
    scene["cleanup_broken_name"] = broken.name
    halo_pack_editor.unregister()
    bpy.ops.wm.save_as_mainfile(filepath=str(test_root / "cleanup.blend"))
    print("RESOURCE_CLEANUP_PREPARE_OK")

elif mode == "cleanup_reopen":
    bpy.ops.wm.open_mainfile(filepath=str(test_root / "cleanup.blend"))
    scene = bpy.context.scene
    removed_names = json.loads(scene["cleanup_remove"])
    protected_names = json.loads(scene["cleanup_protect"])
    assert all(bpy.data.images.get(name) for name in removed_names + protected_names)
    # Register recovers all sources before automatically pruning dead Temp IDs.
    halo_pack_editor.register()
    assert all(bpy.data.images.get(name) is None for name in removed_names)
    assert all(bpy.data.images.get(name) for name in protected_names)
    assert bpy.data.images[protected_names[0]].packed_file
    assert len(json.loads(scene.halo_project["halo_temp_cleanup_json"])) == 2
    broken = bpy.data.objects[scene["cleanup_broken_name"]]
    assert broken.halo_node.texture == "demo:textures/halo/gone.png"
    assert node_images(broken)[0].get("halo_missing_texture")
    assert "gone.png" in json.dumps(blender_scene.sync_definition_from_scene(scene, "demo:cleanup"))
    assert any("gone.png" in warning for warning in json.loads(scene.halo_project["halo_resource_warnings"]))
    # Memory-resident pixels survive cleanup even when there is no disk PNG.
    image = bpy.data.images.load(str(test_root / "cleanup-source/assets/demo/textures/halo/shared.png"), check_existing=False)
    assert len(image.pixels) == 16
    image.filepath_raw = str(test_root / "Temp/halo_pack_edit_memory/missing.png")
    assert image.has_data
    memory_name = image.name
    assert not resource_store.cleanup_missing_temp_images()
    assert bpy.data.images.get(memory_name) is image
    # Only remove these deliberately broken, unrelated test IDs here so
    # Blender Pack Resources can be checked without unrelated-file errors.
    bpy.data.images.remove(image)
    for name in protected_names[1:]:
        bpy.data.images.remove(bpy.data.images[name])
    assert bpy.ops.file.pack_all() == {"FINISHED"}
    bpy.ops.wm.save_as_mainfile(filepath=str(test_root / "cleanup-resaved.blend"))
    bpy.ops.wm.open_mainfile(filepath=str(test_root / "cleanup-resaved.blend"))
    assert all(bpy.data.images.get(name) is None for name in removed_names)
    assert bpy.ops.file.pack_all() == {"FINISHED"}
    print("RESOURCE_CLEANUP_REOPEN_OK unused+bound+packed+pixels+unrelated+json+pack_all")

else:
    raise ValueError(mode)
