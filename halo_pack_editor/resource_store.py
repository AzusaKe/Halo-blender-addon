"""Portable Halo resources: packed Images plus per-source archives in .blend.

Only Halo-owned resources are touched. The archive also retains files Blender
does not display (labPBR, PNG metadata, pack icons and unknown assets).
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import shutil
import tempfile
import uuid
import zipfile
from pathlib import Path


def _path(value):
    import bpy
    return Path(bpy.path.abspath(str(value))).resolve() if value else None


def _archive(source):
    import bpy
    name = source.get("halo_resource_archive", "")
    text = bpy.data.texts.get(name) if name else None
    if text is None:
        return None
    data = base64.b64decode(text.as_string().encode("ascii"))
    if hashlib.sha256(data).hexdigest() != text.get("halo_archive_sha256"):
        raise ValueError("内嵌资源校验失败；保留原数据，请从备份恢复")
    return data


def _copy_missing(source, destination):
    if not Path(destination).is_file():
        shutil.copy2(source, destination)
    return str(destination)


def upgrade_legacy_sources(scene):
    """Bind old single-source/local definitions without changing their JSON."""
    project = scene.halo_project
    for item in project.definitions:
        if item.source_id and any(s.source_id == item.source_id for s in project.sources):
            continue
        root = next((obj for obj in scene.objects if obj.get("halo_role") == "definition_root"
                     and obj.get("halo_definition_id") == item.definition_id), None)
        pack_root = str((root.get("halo_pack_root") if root else "") or project.pack_root or "")
        source = next((s for s in project.sources if s.pack_root == pack_root), None)
        if source is None:
            source = project.sources.add()
            source.source_id = uuid.uuid4().hex
            source.pack_root = pack_root
            source.source_path = project.source_path if pack_root == project.pack_root else ""
            source.source_kind = ("ZIP" if source.source_path.lower().endswith(".zip") else
                                  "FOLDER" if source.source_path else "LOCAL")
            source.name = Path(source.source_path).stem if source.source_path else "旧项目本地资源"
        item.source_id = source.source_id
        if root is not None:
            root["halo_source_id"] = source.source_id
    for source in project.sources:
        source.definition_count = sum(item.source_id == source.source_id for item in project.definitions)


def _relative_image_path(image, old_root):
    from .materials import split_resource_id
    for value in (image.get("halo_source_path"), image.filepath):
        path = _path(value)
        if path is not None and old_root is not None and path.is_relative_to(old_root):
            return path.relative_to(old_root).as_posix()
    resource = image.get("halo_resource_path")
    if resource:
        return str(resource)
    texture_id = image.get("halo_texture_id")
    if not texture_id:
        return ""
    namespace, relative = split_resource_id(texture_id)
    if not Path(relative).suffix:
        relative += ".png"
    return f"assets/{namespace}/{relative}"


def _source_images(scene, source, old_root):
    """Associate by actual object/material links, never globally by texture ID."""
    import bpy
    definition_ids = {item.definition_id for item in scene.halo_project.definitions
                      if item.source_id == source.source_id}
    images = {}
    for obj in scene.objects:
        if obj.get("halo_role") != "primitive" or obj.get("halo_definition_id") not in definition_ids:
            continue
        for material in obj.data.materials:
            if material and material.use_nodes:
                for node in material.node_tree.nodes:
                    if node.type == "TEX_IMAGE" and node.image:
                        images[node.image.as_pointer()] = node.image
    # Also repair old, unused Halo image datablocks. Blender's Pack Resources
    # visits those too, so leaving stale paths would preserve the user's error.
    for image in bpy.data.images:
        path = _path(image.get("halo_source_path") or image.filepath)
        if image.get("halo_texture_id") and (
            image.get("halo_resource_source_id") == source.source_id or
            (old_root is not None and path is not None and path.is_relative_to(old_root))
        ):
            images[image.as_pointer()] = image
    return [(image, _relative_image_path(image, old_root)) for image in images.values()
            if not image.get("halo_missing_texture") and not image.get("halo_generated_texture")]


def _restore_image(image, target):
    """Preserve original packed PNG bytes; do not re-render or color-convert."""
    from .materials import pack_halo_image
    if image.is_dirty:
        pack_halo_image(image)
    if not image.packed_file and image.has_data and not target.is_file():
        # A legacy file may still have its last pixels in memory even though
        # the external PNG has vanished. pack() can use a dirty pixel buffer.
        try:
            image.pack()
        except RuntimeError:
            pixels = image.pixels[:]
            if pixels:
                image.pixels = pixels
                image.pack()
    if image.packed_file:
        data = bytes(image.packed_file.data)
        if not target.is_file() or target.read_bytes() != data:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
    if not target.is_file():
        raise FileNotFoundError(str(target))
    # filepath invokes Blender's reload/repack callback, which still consults
    # the old ImagePackedFile paths and reports the same missing-Temp error.
    # Update both paths without reloading or re-encoding the packed PNG.
    image.filepath_raw = str(target)
    for packed in image.packed_files:
        packed.filepath = str(target)
    image["halo_source_path"] = str(target)
    if not image.packed_file and not image.has_data:
        image.reload()
    pack_halo_image(image)
    if not image.packed_file:
        raise RuntimeError(f"无法内嵌贴图：{image.name}")


def _is_halo_temp_path(path):
    """Recognize this add-on's former extraction roots, including other PCs."""
    import bpy
    if path is None:
        return False
    parts = [part.lower() for part in path.parts]
    cache_index = next((i for i, part in enumerate(parts)
                        if part.startswith(("halo_pack_edit_", "halo_pack_import_", "halo_pack_folder_"))), None)
    if cache_index is None:
        return False
    # A saved path may belong to another Windows user/machine, so checking
    # only the current process's tempfile.gettempdir() is not sufficient.
    if any(part in {"temp", "tmp"} for part in parts[:cache_index]):
        return True
    return any(root and path.is_relative_to(_path(root))
               for root in (tempfile.gettempdir(), bpy.app.tempdir))


def cleanup_missing_temp_images(scene=None):
    """Remove unrecoverable Halo Temp Image IDs, never files or JSON references.

    Run after source recovery. Packed data, live pixels, generated images and
    user images outside Halo's former Temp roots are explicitly protected.
    """
    import bpy
    from .materials import _new_placeholder_image
    protected = set()
    protected_sources, protected_roots = set(), []
    if scene is not None:
        # A manual single-scene repair must not delete images another scene
        # could still recover. The all-scene load/save hooks handle those later.
        for other in bpy.data.scenes:
            if other == scene:
                continue
            for source in other.halo_project.sources:
                protected_sources.add(source.source_id)
                root = _path(source.pack_root)
                if root is not None:
                    protected_roots.append(root)
            for obj in other.objects:
                for material in getattr(obj.data, "materials", ()):
                    if material and material.use_nodes:
                        protected.update(node.image.as_pointer() for node in material.node_tree.nodes
                                         if node.type == "TEX_IMAGE" and node.image)
    removed = []
    for image in list(bpy.data.images):
        if (image.library or image.source != "FILE" or image.packed_files or image.has_data
                or image.get("halo_generated_texture") or image.get("halo_missing_texture")
                or image.as_pointer() in protected
                or image.get("halo_resource_source_id") in protected_sources):
            continue
        paths = [_path(value) for value in (image.filepath, image.get("halo_source_path")) if value]
        if any(path.is_file() for path in paths) or not any(_is_halo_temp_path(path) for path in paths):
            continue
        if any(path.is_relative_to(root) for path in paths for root in protected_roots):
            continue
        label = f"{image.name} ({image.filepath})"
        texture_id = image.get("halo_texture_id") or image.name
        name = "Missing Halo Texture - " + texture_id
        placeholder = bpy.data.images.get(name)
        if placeholder is None or not placeholder.get("halo_missing_texture"):
            placeholder = _new_placeholder_image(texture_id)
        # Keep shader connections valid while removing the broken file-backed
        # datablock that Blender's Pack Resources otherwise keeps visiting.
        image.user_remap(placeholder)
        bpy.data.images.remove(image)
        removed.append(label)
        print("Halo：已清理失效 Temp 图片数据块：", label)
    if removed:
        for owner in ([scene] if scene is not None else bpy.data.scenes):
            if owner.halo_project.definitions:
                owner.halo_project["halo_temp_cleanup_json"] = json.dumps(removed, ensure_ascii=False)
    return removed


def ensure_resources(scene, *, restore_saved=False, rebind=True):
    """Recover old caches and partial loss, returning an actionable report.

    On load a saved archive gets its own working copy. Opening a second .blend
    or an older save must not overwrite another open project's editable cache.
    """
    from . import blender_scene
    from .materials import assign_primitive_materials, load_texture_image
    project = scene.halo_project
    upgrade_legacy_sources(scene)
    report = {"restored_sources": 0, "packed_images": 0, "warnings": []}
    persistent_parent = Path(blender_scene.edit_cache_parent()).resolve()
    for source in project.sources:
        old_root = _path(source.pack_root)
        bindings = _source_images(scene, source, old_root)
        try:
            saved = _archive(source)
            source.pop("halo_resource_error", None)
            migrate = (old_root is None or not old_root.is_dir() or
                       not old_root.is_relative_to(persistent_parent) or (restore_saved and saved is not None))
            if migrate:
                root = Path(blender_scene._new_edit_root())
                if saved is not None:
                    blender_scene._safe_extract(io.BytesIO(saved), str(root))
                elif old_root is not None and old_root.is_dir():
                    shutil.copytree(old_root, root, dirs_exist_ok=True)
                elif source.source_kind == "FOLDER" and os.path.isdir(source.source_path):
                    shutil.copytree(source.source_path, root, dirs_exist_ok=True)
                elif source.source_kind == "ZIP" and zipfile.is_zipfile(source.source_path):
                    blender_scene._safe_extract(source.source_path, str(root))
                elif source.source_kind != "LOCAL":
                    source["halo_incomplete_resources"] = True
                source.pack_root = str(root)
                report["restored_sources"] += 1
            else:
                root = old_root
                if saved is not None:
                    blender_scene._safe_extract(io.BytesIO(saved), str(root), overwrite=False)
            if source.get("halo_incomplete_resources"):
                # Keep this warning through subsequent saves, and allow a
                # restored original source to fill files images cannot recover.
                if source.source_kind == "FOLDER" and os.path.isdir(source.source_path):
                    shutil.copytree(source.source_path, root, dirs_exist_ok=True, copy_function=_copy_missing)
                    source.pop("halo_incomplete_resources", None)
                elif source.source_kind == "ZIP" and zipfile.is_zipfile(source.source_path):
                    blender_scene._safe_extract(source.source_path, str(root), overwrite=False)
                    source.pop("halo_incomplete_resources", None)
                else:
                    report["warnings"].append(f"{source.name}：原包不可用，已尽力恢复图片；附图/其他资源可能不完整")
            for image, relative in bindings:
                try:
                    if not relative:
                        continue
                    target = (root / relative).resolve()
                    target.relative_to(root.resolve())
                    image["halo_resource_source_id"] = source.source_id
                    image["halo_resource_path"] = relative
                    # Recover a partially deleted legacy cache from its source
                    # without replacing already edited files in that cache.
                    if not target.is_file() and not image.packed_file:
                        if source.source_kind == "ZIP" and zipfile.is_zipfile(source.source_path):
                            blender_scene._safe_extract(source.source_path, str(root), overwrite=False)
                        elif source.source_kind == "FOLDER" and os.path.isdir(source.source_path):
                            origin = (Path(source.source_path).resolve() / relative).resolve()
                            origin.relative_to(Path(source.source_path).resolve())
                            if origin.is_file():
                                target.parent.mkdir(parents=True, exist_ok=True)
                                shutil.copy2(origin, target)
                    _restore_image(image, target)
                    report["packed_images"] += 1
                except Exception as exc:
                    report["warnings"].append(f"{source.name} / {relative}：{exc}")
        except Exception as exc:
            source["halo_resource_error"] = str(exc)
            report["warnings"].append(f"{source.name}：{exc}")
    for obj in scene.objects:
        if obj.get("halo_role") == "definition_root":
            source = next((s for s in project.sources if s.source_id == obj.get("halo_source_id")), None)
            if source is not None:
                obj["halo_pack_root"] = source.pack_root
    if project.sources:
        source = project.sources[min(max(0, project.active_source_index), len(project.sources) - 1)]
        project.pack_root = source.pack_root
    for obj in scene.objects:
        if obj.get("halo_role") != "primitive":
            continue
        node = obj.halo_node
        pack_root = blender_scene.definition_pack_root(scene, obj.get("halo_definition_id", ""))
        for texture_id in {node.texture, node.inner_texture if node.primitive_type == "ring" else ""} - {""}:
            image = load_texture_image(texture_id, pack_root)
            if image.get("halo_missing_texture"):
                report["warnings"].append(f"{obj.name}：缺失 {texture_id}，请重新链接 PNG")
        if rebind:
            parent_node = getattr(obj.parent, "halo_node", None)
            assign_primitive_materials(obj, node.texture, node.inner_texture or None,
                                       pack_root,
                                       glowing=bool(parent_node.glowing) if parent_node else True)
    project["halo_resource_warnings"] = json.dumps(report["warnings"], ensure_ascii=False)
    return report


def embed_resources(scene):
    """Called before save: snapshot every retained source, including sidecars."""
    import bpy
    report = ensure_resources(scene, rebind=False)
    for source in scene.halo_project.sources:
        try:
            if source.get("halo_resource_error"):
                continue  # Never replace a corrupt/unreadable backup with a partial one.
            root = _path(source.pack_root)
            if root is None or not root.is_dir():
                raise FileNotFoundError(source.pack_root)
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for path in sorted(root.rglob("*")):
                    if path.is_symlink() or not path.resolve().is_relative_to(root):
                        raise ValueError(f"不内嵌指向缓存外的资源：{path}")
                    if path.is_file():
                        # Fixed ZIP timestamps make unchanged snapshots stable.
                        info = zipfile.ZipInfo(path.relative_to(root).as_posix())
                        info.compress_type = zipfile.ZIP_DEFLATED
                        archive.writestr(info, path.read_bytes())
            data = buffer.getvalue()
            digest = hashlib.sha256(data).hexdigest()
            name = source.get("halo_resource_archive", "")
            text = bpy.data.texts.get(name) if name else None
            if text is None:
                text = bpy.data.texts.new(f".halo_resources_{source.source_id}.zip.base64")
                text.use_fake_user = True
                source["halo_resource_archive"] = text.name
            if text.get("halo_archive_sha256") != digest:
                encoded = base64.b64encode(data).decode("ascii")
                text.clear()
                text.write("\n".join(encoded[i:i + 120] for i in range(0, len(encoded), 120)))
                text["halo_archive_sha256"] = digest
                text["halo_resource_source_id"] = source.source_id
        except Exception as exc:
            report["warnings"].append(f"{source.name}：资源内嵌失败 ({exc})")
    scene.halo_project["halo_resource_warnings"] = json.dumps(report["warnings"], ensure_ascii=False)
    # Cleared sources should not leave large hidden archives in future saves.
    retained = {s.get("halo_resource_archive") for owner in bpy.data.scenes
                for s in owner.halo_project.sources}
    for text in list(bpy.data.texts):
        if text.get("halo_resource_source_id") and text.name not in retained:
            bpy.data.texts.remove(text)
    return report
