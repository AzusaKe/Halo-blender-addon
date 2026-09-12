"""Bridge between Halo JSON documents and Blender's scene graph.

This module is deliberately the only place that knows how a parsed Halo
definition becomes Blender objects.  It accepts either the typed core models
or plain dictionaries, but always keeps a copy of the source JSON on the
scene objects.  That makes importing a pack produced by a newer mod safe:
unknown fields survive an edit/export round trip.
"""

from __future__ import annotations

import copy
import json
import math
import os
import shutil
import tempfile
import uuid
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping

try:
    import bpy
    from mathutils import Matrix, Quaternion, Vector
except ImportError:  # pragma: no cover - Blender-only module
    bpy = None
    Matrix = None
    Quaternion = Vector = None

from .geometry import (
    blender_rotation_to_mc_euler,
    billboard_mesh,
    blender_to_mc,
    mc_rotation_quaternion,
    mc_to_blender,
    ring_mesh,
    uniform_scale,
)
from .materials import assign_mesh_material, assign_primitive_materials
from .obj_mesh import build_blender_mesh, load_obj_resource
from .core.resource_paths import normalize_staged_resources


COLLECTION_NAME = "Halo Pack Editor"
ROOT_ROLE = "definition_root"
GROUP_ROLE = "group"
PRIMITIVE_ROLE = "primitive"
HEAD_ROLE = "head_preview"
DEFAULT_PACK_DESCRIPTION = "Halo Pack Editor export"
DEFAULT_MANIFEST = {
    "pack": {
        "pack_format": 15,
        "supported_formats": {"min_inclusive": 15, "max_inclusive": 88},
        "min_format": [15, 0],
        "max_format": [88, 0],
        "description": DEFAULT_PACK_DESCRIPTION,
    }
}
UUID_NAMESPACE = uuid.UUID("f4d8d3c2-2d16-4c04-98cb-5fd8af7e2b64")

# Typed core projects are kept in memory while a Blender scene is open.  The
# scene stores the lossless JSON strings needed for .blend persistence; this
# cache is an optional accelerator for validation/clients that want core
# models and is intentionally rebuilt after a .blend is reopened.
_CORE_PROJECTS: dict[str, Any] = {}


def _json_copy(value: Any) -> Any:
    return copy.deepcopy(value)


def _complete_manifest(value: Any) -> dict[str, Any]:
    """Return valid Minecraft pack metadata without dropping unknown keys."""

    manifest = _json_copy(dict(value)) if isinstance(value, Mapping) else {}
    source_pack = manifest.get("pack")
    pack = _json_copy(dict(source_pack)) if isinstance(source_pack, Mapping) else {}
    pack.setdefault("pack_format", 15)
    pack.setdefault("supported_formats", {"min_inclusive": 15, "max_inclusive": 88})
    pack.setdefault("min_format", [15, 0])
    pack.setdefault("max_format", [88, 0])
    description = pack.get("description")
    if description is None or (isinstance(description, str) and not description.strip()):
        pack["description"] = DEFAULT_PACK_DESCRIPTION
    manifest["pack"] = pack
    return manifest


def _as_dict(value: Any) -> dict[str, Any]:
    """Convert a core model/dataclass/mapping into a mutable JSON dictionary."""

    if isinstance(value, Mapping):
        return _json_copy(dict(value))
    for method in ("to_raw", "to_dict", "as_dict", "serialize"):
        callback = getattr(value, method, None)
        if callable(callback):
            try:
                result = callback()
                if isinstance(result, Mapping):
                    return _json_copy(dict(result))
            except Exception:
                pass
    raw = getattr(value, "raw", None) or getattr(value, "raw_json", None)
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, Mapping):
                return _json_copy(dict(parsed))
        except (TypeError, ValueError):
            pass
    # Dataclasses and the core records use public attributes.  The fallback is
    # intentionally conservative; the original JSON is preferred whenever it
    # is available.
    result: dict[str, Any] = {}
    for key in (
        "id", "version", "orientation_mode", "allow_angular_momentum",
        "hide_on_sleep", "display_in_invisible", "layers", "shape",
        "animation", "positioning", "damping", "startup", "shutdown",
    ):
        if hasattr(value, key):
            item = getattr(value, key)
            if hasattr(item, "__dict__") and not isinstance(item, (str, bytes)):
                item = _as_dict(item)
            result[key] = _json_copy(item)
    if hasattr(value, "model") and not result.get("layers"):
        model = getattr(value, "model")
        if hasattr(model, "groups"):
            result["layers"] = [_as_group_dict(group) for group in getattr(model, "groups")]
    return result


def _optional_value(value: Any, default=None):
    if value is None:
        return default
    if hasattr(value, "is_present") and callable(value.is_present):
        return value.get() if value.is_present() else default
    if hasattr(value, "isPresent") and callable(value.isPresent):
        return value.get() if value.isPresent() else default
    return value


def _vec(value: Any, size: int, default: Iterable[float]) -> list[float]:
    value = _optional_value(value, default)
    if value is None:
        value = default
    if hasattr(value, "x"):
        result = [value.x, value.y]
        if size > 2:
            result.append(value.z)
    else:
        try:
            result = list(value)
        except TypeError:
            result = list(default)
    result = [float(v) for v in result[:size]]
    while len(result) < size:
        result.append(float(list(default)[len(result)]))
    return result


def _number(value: Any, default: float) -> float:
    """Convert a JSON number without treating the valid value zero as absent."""

    try:
        return float(default if value is None else value)
    except (TypeError, ValueError):
        return float(default)


def _as_group_dict(value: Any) -> dict[str, Any]:
    raw = _as_dict(value)
    if raw:
        # Convert a typed group when it did not expose a serializer.
        if "position" not in raw and hasattr(value, "position"):
            raw["position"] = _vec(getattr(value, "position"), 3, (0, 0, 0))
        if "rotation" not in raw and hasattr(value, "rotation"):
            raw["rotation"] = _vec(getattr(value, "rotation"), 3, (0, 0, 0))
        if "scale" not in raw and hasattr(value, "scale"):
            raw["scale"] = float(getattr(value, "scale"))
        if "children" not in raw and hasattr(value, "children"):
            raw["children"] = [_as_group_dict(child) for child in getattr(value, "children")]
        if "primitives" not in raw and hasattr(value, "primitives"):
            raw["primitives"] = [_as_dict(primitive) for primitive in getattr(value, "primitives")]
    return raw


def normalise_definition(raw: Mapping[str, Any], fallback_id: str = "minecraft:halo") -> dict[str, Any]:
    """Make old ``shape`` definitions consumable by the object builder."""

    result = _json_copy(dict(raw))
    result.setdefault("id", fallback_id)
    if "layers" not in result and isinstance(result.get("shape"), Mapping):
        shape = dict(result["shape"])
        shape_type = str(shape.get("type", ""))
        if shape_type == "multi_billboard":
            result["layers"] = [
                {"position": [0, 0, 0], "rotation": [0, 0, 0], "scale": 1, "primitive": dict(primitive)}
                for primitive in shape.get("layers", [])
                if isinstance(primitive, Mapping)
            ]
        elif shape_type in ("billboard", "ring"):
            result["layers"] = [{"position": [0, 0, 0], "rotation": [0, 0, 0], "scale": 1, "primitive": shape}]
    result.setdefault("layers", [])
    result.setdefault("animation", {})
    result.setdefault("positioning", {"offset": [0, 0, 0], "scale": 1})
    return result


def _definition_id(raw: Mapping[str, Any], fallback: str) -> str:
    value = raw.get("id", fallback)
    return str(value)


def _namespace(definition_id: str) -> str:
    return definition_id.split(":", 1)[0] if ":" in definition_id else "minecraft"


def _safe_name(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in str(value))[:180] or "halo"


def _deterministic_uuid(definition_id: str, path: str) -> str:
    return uuid.uuid5(UUID_NAMESPACE, f"{definition_id}\x00{path}").hex


def _read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8-sig") as stream:
        value = json.load(stream)
    if not isinstance(value, Mapping):
        raise ValueError("definition JSON must contain an object")
    return dict(value)


def _safe_extract(source, destination: str, *, overwrite: bool = True) -> None:
    root = Path(destination).resolve()
    with zipfile.ZipFile(source) as archive:
        for member in archive.infolist():
            # UNIX symlinks inside ZIPs can escape the destination even when
            # their member name itself is harmless.
            if ((member.external_attr >> 16) & 0o170000) == 0o120000:
                raise ValueError(f"unsafe ZIP symlink: {member.filename}")
            target = (root / member.filename).resolve()
            try:
                target.relative_to(root)
            except ValueError as exc:
                raise ValueError(f"unsafe ZIP member path: {member.filename}") from exc
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            if not overwrite and target.is_file():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as source_stream, target.open("wb") as target_stream:
                shutil.copyfileobj(source_stream, target_stream)


def edit_cache_parent() -> str:
    """Persistent runtime storage; never silently fall back to OS Temp."""
    if bpy is not None:
        cache_parent = bpy.utils.user_resource("DATAFILES", path="halo_pack_editor/cache", create=True)
        if cache_parent:
            return cache_parent
    raise RuntimeError("无法建立 Blender 用户资源目录；请检查目录写入权限")


def _new_edit_root(prefix: str = "halo_pack_edit_") -> str:
    """Create a writable cache that can survive reopening a saved .blend."""
    return tempfile.mkdtemp(prefix=prefix, dir=edit_cache_parent())


def _copy_folder_to_edit_root(source: str) -> str:
    destination = _new_edit_root("halo_pack_folder_")
    shutil.copytree(source, destination, dirs_exist_ok=True)
    return destination


def read_pack(path: str | os.PathLike[str]) -> dict[str, Any]:
    """Read a ZIP or unpacked resource pack into import-ready dictionaries."""

    source = str(Path(path).expanduser().resolve())
    if not os.path.exists(source):
        raise FileNotFoundError(source)
    core_project = None
    try:
        # ``core.pack_io`` reads ZIP members losslessly and records malformed
        # definitions without aborting sibling imports.  We still extract a
        # ZIP below because Blender's image loader needs real filesystem paths.
        from .core.pack_io import import_pack
        core_project = import_pack(source, strict=False)
    except Exception:
        # A hand-authored pack can be useful even before the optional core
        # parser knows its newest field; the permissive fallback below keeps
        # the Blender editor available in that case.
        core_project = None
    temporary = None
    root = source
    if zipfile.is_zipfile(source):
        temporary = _new_edit_root("halo_pack_import_")
        _safe_extract(source, temporary)
        root = temporary
    elif os.path.isdir(source):
        temporary = _copy_folder_to_edit_root(source)
        root = temporary
    else:
        raise ValueError("资源包必须是 ZIP 文件或解包目录")
    root_path = Path(root)
    manifest_path = root_path / "pack.mcmeta"
    if core_project is not None and isinstance(getattr(core_project, "pack_mcmeta", None), Mapping):
        manifest = _json_copy(core_project.pack_mcmeta)
    elif manifest_path.is_file():
        try:
            manifest = _read_json(manifest_path)
        except (OSError, ValueError, json.JSONDecodeError):
            manifest = _json_copy(DEFAULT_MANIFEST)
    else:
        manifest = _json_copy(DEFAULT_MANIFEST)
    manifest = _complete_manifest(manifest)
    definitions = []
    if core_project is not None:
        for asset in getattr(core_project, "definitions", ()):
            raw = _json_copy(getattr(getattr(asset, "document", None), "data", getattr(asset, "raw", {})))
            definition_id = str(getattr(asset, "identifier", None) or raw.get("id", ""))
            if not definition_id:
                continue
            definitions.append({
                "id": definition_id,
                "raw": normalise_definition(raw, definition_id),
                "source_path": str(getattr(asset, "source_path", "")),
                "error": "",
            })
    definitions_root = root_path / "assets"
    # Core parsing is lossless for valid assets; scan the extracted tree too
    # so a malformed definition is still represented in the Blender project
    # and can be repaired from the raw JSON editor.  Valid assets already
    # supplied by core are not duplicated.
    known_source_paths = {str(item.get("source_path", "")).replace("\\", "/") for item in definitions}
    if definitions_root.is_dir():
        for file_path in sorted(definitions_root.glob("*/halo_definitions/**/*.json")):
            relative = file_path.relative_to(root_path).as_posix()
            if relative in known_source_paths:
                continue
            namespace = relative.split("/", 2)[1] if relative.count("/") >= 2 else "minecraft"
            fallback = f"{namespace}:{file_path.stem}"
            try:
                raw = normalise_definition(_read_json(file_path), fallback)
                definition_id = _definition_id(raw, fallback)
                definitions.append({"id": definition_id, "raw": raw, "source_path": relative, "error": ""})
            except Exception as exc:
                definitions.append({"id": fallback, "raw": normalise_definition({"id": fallback}, fallback), "source_path": relative, "error": str(exc)})
    # Work only on the editable cache, never the user's source pack.  This
    # makes legacy upper-case assets immediately previewable and editable on
    # case-sensitive hosts as well as guaranteeing valid IDs on export.
    normalize_staged_resources(root_path, (item["raw"] for item in definitions))
    return {
        "source_path": source,
        "root": root,
        "temporary_root": temporary or "",
        "manifest": manifest,
        "definitions": definitions,
        "core_project": core_project,
    }


def _ensure_collection(scene):
    collection = next((
        child for child in scene.collection.children
        if child.get("halo_editor_collection") or child.name == COLLECTION_NAME
    ), None)
    if collection is None:
        collection = bpy.data.collections.new(COLLECTION_NAME)
        collection["halo_editor_collection"] = True
        collection["halo_preview_scene"] = scene.name
        scene.collection.children.link(collection)
    return collection


def _project_definition(scene, definition_id: str):
    project = getattr(scene, "halo_project", None)
    if project is None:
        return None
    return next((item for item in project.definitions if item.definition_id == definition_id), None)


def unique_definition_id(scene, requested_id: str) -> str:
    """Return an unused resource ID, suffixing only the in-memory copy."""

    requested_id = str(requested_id or "minecraft:halo").strip() or "minecraft:halo"
    if ":" not in requested_id:
        requested_id = "minecraft:" + requested_id
    used = {item.definition_id for item in scene.halo_project.definitions}
    if requested_id not in used:
        return requested_id
    namespace, path = requested_id.split(":", 1)
    index = 2
    while f"{namespace}:{path}_{index}" in used:
        index += 1
    return f"{namespace}:{path}_{index}"


def ensure_local_source(scene):
    """Return the writable source used by newly authored definitions/assets."""

    project = scene.halo_project
    source = next((entry for entry in getattr(project, "sources", ()) if entry.source_kind == "LOCAL"), None)
    if source is None:
        source = project.sources.add()
        source.source_id = uuid.uuid4().hex
        source.name = "本地编辑资源"
        source.source_kind = "LOCAL"
        source.source_path = ""
        source.definition_count = 0
    if not source.pack_root or not os.path.isdir(source.pack_root):
        source.pack_root = _new_edit_root()
    return source


def definition_pack_root(scene, definition_id: str) -> str:
    """Resolve the editable resource root associated with one definition."""

    project = getattr(scene, "halo_project", None)
    if project is None:
        return ""
    item = _project_definition(scene, definition_id)
    source_id = str(getattr(item, "source_id", "") or "") if item is not None else ""
    if source_id:
        source = next((entry for entry in getattr(project, "sources", ()) if entry.source_id == source_id), None)
        if source is not None:
            if source.pack_root and os.path.isdir(source.pack_root):
                return source.pack_root
    root = next((
        obj for obj in scene.objects
        if obj.get("halo_role") == ROOT_ROLE and obj.get("halo_definition_id") == definition_id
    ), None) if bpy is not None else None
    if root is not None and root.get("halo_pack_root") and os.path.isdir(root.get("halo_pack_root")):
        return str(root.get("halo_pack_root"))
    return str(getattr(project, "pack_root", "") or "")


def ensure_source_roots(scene) -> int:
    """Rebuild missing editable caches after reopening a .blend file."""
    from .resource_store import ensure_resources
    return ensure_resources(scene)["restored_sources"]


def _store_node_props(obj, raw: Mapping[str, Any], definition_id: str, role: str, node_uuid: str, primitive=None):
    obj["halo_role"] = role
    obj["halo_uuid"] = node_uuid
    obj["halo_definition_id"] = definition_id
    obj["halo_raw_json"] = json.dumps(dict(raw), ensure_ascii=False, separators=(",", ":"))
    obj["halo_parent_uuid"] = getattr(obj.parent, "get", lambda *_: "")("halo_uuid", "") if obj.parent else ""
    node = getattr(obj, "halo_node", None)
    if node is not None:
        obj["halo_property_update_guard"] = True
        try:
            node.uuid = node_uuid
            node.definition_id = definition_id
            node.role = role if role in {"group", "primitive"} else "group"
            node.node_id = str(raw.get("id", "")) if role == GROUP_ROLE else ""
            position = _vec(raw.get("position"), 3, (0, 0, 0))
            rotation = _vec(raw.get("rotation"), 3, (0, 0, 0))
            node.position = position
            node.rotation = rotation
            node.scale = float(raw.get("scale", 1.0) or 1.0)
            node.glowing = bool(raw.get("glowing", True))
            node.inherit_alpha = bool(raw.get("inherit_alpha", True))
            node.inherit_glow = bool(raw.get("inherit_glow", True))
            node.raw_json = json.dumps(dict(raw), ensure_ascii=False, indent=2)
            node.parent_uuid = obj.parent.get("halo_uuid", "") if obj.parent else ""
            animation = raw.get("animation", {})
            node.animation_json = json.dumps(animation, ensure_ascii=False, indent=2) if animation else "{}"
            if primitive is not None:
                node.primitive_type = str(primitive.get("type", "billboard"))
                node.texture = str(primitive.get("texture", primitive.get("outer_texture", "")))
                node.inner_texture = str(primitive.get("inner_texture", ""))
                node.size = _vec(primitive.get("size"), 2, (1.0, 1.0))
                node.mesh_model = str(primitive.get("model", ""))
                node.mesh_size = _vec(primitive.get("size"), 3, (1.0, 1.0, 1.0))
                node.mesh_preserve_proportions = bool(primitive.get("preserve_proportions", False))
                node.mesh_scale = float(primitive.get("scale", 1.0))
                obj["halo_mesh_size_present"] = "size" in primitive
                material = primitive.get("material") if isinstance(primitive.get("material"), Mapping) else {}
                node.mesh_double_sided = bool(material.get("double_sided", True))
                effects = material.get("effects") if isinstance(material.get("effects"), list) else []
                mask = next((effect for effect in effects if isinstance(effect, Mapping)
                             and str(effect.get("type", "")) == "alpha_mask"), None)
                node.mesh_mask_enabled = mask is not None
                node.mesh_mask_texture = str(mask.get("texture", "")) if mask is not None else ""
                node.mesh_mask_mode = str(mask.get("mode", "linear")) if mask is not None else "linear"
                node.mesh_mask_threshold = float(mask.get("threshold", 0.5)) if mask is not None else 0.5
                node.segments = int(primitive.get("segments", 32) or 32)
                node.face_camera = bool(primitive.get("face_camera", False))
                obj["halo_primitive_raw_json"] = json.dumps(dict(primitive), ensure_ascii=False, separators=(",", ":"))
                obj["halo_texture_id"] = node.texture
                obj["halo_inner_texture_id"] = node.inner_texture
                obj["halo_face_camera"] = node.face_camera
        finally:
            obj.pop("halo_property_update_guard", None)
    obj["halo_animation_json"] = json.dumps(raw.get("animation", {}), ensure_ascii=False, separators=(",", ":"))


def _new_empty(collection, name: str, role: str):
    obj = bpy.data.objects.new(name, None)
    obj.empty_display_type = "CUBE"
    obj.empty_display_size = 0.075
    collection.objects.link(obj)
    obj["halo_role"] = role
    return obj


def _set_group_transform(obj, raw: Mapping[str, Any]):
    obj.location = mc_to_blender(_vec(raw.get("position"), 3, (0, 0, 0)))
    obj.rotation_mode = "QUATERNION"
    quat = mc_rotation_quaternion(_vec(raw.get("rotation"), 3, (0, 0, 0)))
    if quat is not None:
        obj.rotation_quaternion = quat
    scale = float(raw.get("scale", 1.0) or 1.0)
    obj.scale = (scale, scale, scale)


def _lock_managed_transform(obj):
    """Prevent native G/R/S from creating unsaved schema-incompatible state."""

    obj.lock_location = (True, True, True)
    obj.lock_rotation = (True, True, True)
    obj.lock_rotation_w = True
    obj.lock_scale = (True, True, True)
    obj["halo_transform_panel_owned"] = True


def reset_primitive_transform(obj):
    """Restore a primitive Mesh to its schema-defined identity transform."""

    obj["halo_property_update_guard"] = True
    try:
        obj.location = (0.0, 0.0, 0.0)
        obj.rotation_mode = "QUATERNION"
        obj.rotation_quaternion = (1.0, 0.0, 0.0, 0.0)
        obj.scale = (1.0, 1.0, 1.0)
        _lock_managed_transform(obj)
    finally:
        obj.pop("halo_property_update_guard", None)


def enforce_managed_transform_locks(scene, restore: bool = True):
    """Apply panel-owned transform policy to imported and reopened objects."""

    for obj in scene.objects:
        role = obj.get("halo_role")
        if role not in {ROOT_ROLE, GROUP_ROLE, PRIMITIVE_ROLE}:
            continue
        _lock_managed_transform(obj)
        if not restore:
            continue
        if role == GROUP_ROLE and getattr(obj, "halo_node", None) is not None:
            _set_group_transform(obj, {
                "position": list(obj.halo_node.position),
                "rotation": list(obj.halo_node.rotation),
                "scale": float(obj.halo_node.scale),
            })
        elif role == PRIMITIVE_ROLE:
            reset_primitive_transform(obj)


def _make_primitive(collection, group_obj, primitive: Mapping[str, Any], definition_id: str, path: str, glowing=True):
    primitive = dict(primitive)
    primitive_type = str(primitive.get("type", "billboard")).lower()
    size = _vec(primitive.get("size"), 2, (1.0, 1.0))
    obj_name = f"{group_obj.name} · {primitive_type}"
    model_error = ""
    if primitive_type == "ring":
        # Blender uses one double-sided surface for both variants.  When an
        # explicit inner texture exists, the material selects it on backfaces;
        # this avoids Cycles ambiguity from the mod's coincident two surfaces.
        mesh = ring_mesh(obj_name, size, int(primitive.get("segments", 32) or 32), False)
        texture = str(primitive.get("outer_texture", primitive.get("texture", "")))
        inner_texture = str(primitive.get("inner_texture", ""))
    elif primitive_type == "mesh":
        texture = str(primitive.get("texture", ""))
        inner_texture = ""
        scene = getattr(bpy.context, "scene", None)
        pack_root = definition_pack_root(scene, definition_id) if scene is not None else ""
        try:
            source = load_obj_resource(str(primitive.get("model", "")), pack_root)
            mesh = build_blender_mesh(
                obj_name,
                source,
                _vec(primitive.get("size"), 3, (1.0, 1.0, 1.0)) if "size" in primitive else None,
                preserve_proportions=bool(primitive.get("preserve_proportions", False)),
                scale=float(primitive.get("scale", 1.0)),
            )
        except (OSError, UnicodeError, ValueError) as exc:
            mesh = billboard_mesh(obj_name + " · missing OBJ", (0.25, 0.25))
            model_error = str(exc)
    else:
        mesh = billboard_mesh(obj_name, size)
        texture = str(primitive.get("texture", ""))
        inner_texture = ""
    obj = bpy.data.objects.new(obj_name, mesh)
    collection.objects.link(obj)
    obj.parent = group_obj
    reset_primitive_transform(obj)
    primitive_uuid = _deterministic_uuid(definition_id, path)
    _store_node_props(obj, primitive, definition_id, PRIMITIVE_ROLE, primitive_uuid, primitive)
    if model_error:
        obj["halo_missing_model"] = True
        obj["halo_model_error"] = model_error
    else:
        obj.pop("halo_missing_model", None)
        obj.pop("halo_model_error", None)
    obj["halo_primitive_index"] = int(path.rsplit("/", 1)[-1]) if path.rsplit("/", 1)[-1].isdigit() else 0
    scene = getattr(bpy.context, "scene", None)
    pack_root = definition_pack_root(scene, definition_id) if scene is not None else ""
    if primitive_type == "mesh":
        material = primitive.get("material") if isinstance(primitive.get("material"), Mapping) else {}
        effects = material.get("effects") if isinstance(material.get("effects"), list) else []
        mask = next((effect for effect in effects if isinstance(effect, Mapping)
                     and str(effect.get("type", "")) == "alpha_mask"), None)
        assign_mesh_material(
            obj, texture, pack_root, glowing=glowing,
            double_sided=bool(material.get("double_sided", True)),
            mask_texture_id=str(mask.get("texture", "")) if mask is not None else "",
            mask_mode=str(mask.get("mode", "linear")) if mask is not None else "linear",
            mask_threshold=float(mask.get("threshold", 0.5)) if mask is not None else 0.5,
        )
    else:
        assign_primitive_materials(obj, texture, inner_texture or None, pack_root, glowing=glowing)
    return obj


def _make_group(collection, parent, raw: Mapping[str, Any], definition_id: str, path: str):
    raw = dict(raw)
    node_id = str(raw.get("id", f"group_{path.replace('/', '_')}"))
    name = f"{node_id} [{path}]"
    obj = _new_empty(collection, _safe_name(name), GROUP_ROLE)
    obj.parent = parent
    _set_group_transform(obj, raw)
    node_uuid = _deterministic_uuid(definition_id, path)
    _store_node_props(obj, raw, definition_id, GROUP_ROLE, node_uuid)
    _lock_managed_transform(obj)
    obj["halo_path"] = path
    obj["halo_original_has_id"] = "id" in raw
    obj["halo_original_primitives_key"] = "primitives" if "primitives" in raw else "primitive" if "primitive" in raw else ""
    primitives = raw.get("primitives")
    if primitives is None and isinstance(raw.get("primitive"), Mapping):
        primitives = [raw["primitive"]]
    if not isinstance(primitives, list):
        primitives = []
    for index, primitive in enumerate(primitives):
        if isinstance(primitive, Mapping):
            _make_primitive(collection, obj, primitive, definition_id, f"{path}/primitive/{index}", bool(raw.get("glowing", True)))
    children = raw.get("children", [])
    if isinstance(children, list):
        for index, child in enumerate(children):
            if isinstance(child, Mapping):
                _make_group(collection, obj, child, definition_id, f"{path}/child/{index}")
    return obj


def _remove_definition_objects(definition_id: str, scene=None):
    objects = scene.objects if scene is not None else bpy.data.objects
    for obj in list(objects):
        if obj.get("halo_definition_id") == definition_id:
            bpy.data.objects.remove(obj, do_unlink=True)


def _remove_head_preview(scene):
    for obj in list(bpy.data.objects):
        if obj.get("halo_role") == HEAD_ROLE and obj.get("halo_preview_scene") == scene.name:
            bpy.data.objects.remove(obj, do_unlink=True)


def _create_definition_pg(
    scene,
    definition_id: str,
    raw: Mapping[str, Any],
    source_path: str = "",
    error: str = "",
    source_id: str = "",
):
    project = scene.halo_project
    item = _project_definition(scene, definition_id)
    if item is None:
        item = project.definitions.add()
    item.definition_id = definition_id
    item.namespace = _namespace(definition_id)
    item.source_path = source_path
    item.source_id = source_id
    item.visible = True
    item.raw_json = json.dumps(dict(raw), ensure_ascii=False, indent=2)
    item.schema_version = str(raw.get("version", project.schema_version or "1.1.0"))
    item.orientation_mode = str(raw.get("orientation_mode", "locked")).lower()
    item.sync_offset = _vec(raw.get("sync_offset"), 3, (0, 0, 0))
    positioning = raw.get("positioning") if isinstance(raw.get("positioning"), Mapping) else {}
    item.positioning_offset = _vec(positioning.get("offset"), 3, (0, 0, 0))
    item.positioning_scale = float(positioning.get("scale", 1.0) or 1.0)
    item.allow_angular_momentum = bool(raw.get("allow_angular_momentum", False))
    item.hide_on_sleep = bool(raw.get("hide_on_sleep", False))
    item.display_in_invisible = bool(raw.get("display_in_invisible", False))
    damping = raw.get("damping") if isinstance(raw.get("damping"), Mapping) else {}
    item.damping_linear_factor = _number(damping.get("linearFactor"), 0.15)
    item.damping_angular_factor = _number(damping.get("angularFactor"), 0.1)
    item.damping_max_linear = _number(damping.get("maxLinearDistance"), 3.0)
    item.damping_max_angular = _number(damping.get("maxAngularDegrees"), 180.0)
    item.damping_angular_momentum_factor = _number(damping.get("angularMomentumFactor"), 0.3)
    item.damping_max_angular_momentum = _number(damping.get("maxAngularMomentumDegrees"), 45.0)
    item.animation_json = json.dumps(raw.get("animation", {}), ensure_ascii=False, indent=2)
    item.startup_json = json.dumps(raw.get("startup", {}), ensure_ascii=False, indent=2)
    item.shutdown_json = json.dumps(raw.get("shutdown", {}), ensure_ascii=False, indent=2)
    item.root_uuid = ""
    if error:
        item["halo_import_error"] = error
    return item


def import_definition_to_scene(
    scene,
    definition: Mapping[str, Any],
    replace: bool = True,
    *,
    source_id: str = "",
    pack_root: str = "",
):
    """Create a root/group/primitive object tree for one definition."""

    definition_id = str(definition.get("id") or _definition_id(definition.get("raw", {}), "minecraft:halo"))
    raw = normalise_definition(definition.get("raw", {}), definition_id)
    collection = _ensure_collection(scene)
    if replace:
        _remove_definition_objects(definition_id, scene)
    item = _create_definition_pg(
        scene,
        definition_id,
        raw,
        str(definition.get("source_path", "")),
        str(definition.get("error", "")),
        source_id,
    )
    root = _new_empty(collection, _safe_name(f"Halo · {definition_id}"), ROOT_ROLE)
    root["halo_definition_id"] = definition_id
    root["halo_uuid"] = _deterministic_uuid(definition_id, "root")
    root["halo_raw_json"] = json.dumps(raw, ensure_ascii=False, indent=2)
    root["halo_pack_root"] = pack_root or definition_pack_root(scene, definition_id)
    root["halo_source_id"] = source_id
    root["halo_source_path"] = str(definition.get("source_path", ""))
    root["halo_positioning_offset"] = _vec(raw.get("positioning", {}).get("offset") if isinstance(raw.get("positioning"), Mapping) else None, 3, (0, 0, 0))
    root["halo_positioning_scale"] = float(raw.get("positioning", {}).get("scale", 1.0) if isinstance(raw.get("positioning"), Mapping) else 1.0)
    _lock_managed_transform(root)
    item.root_uuid = root["halo_uuid"]
    layers = raw.get("layers", [])
    if isinstance(layers, list):
        for index, group in enumerate(layers):
            if isinstance(group, Mapping):
                _make_group(collection, root, group, definition_id, f"layer/{index}")
    scene.halo_project.active_definition = definition_id
    try:
        scene.halo_project.active_definition_index = next(
            i for i, entry in enumerate(scene.halo_project.definitions) if entry.definition_id == definition_id
        )
    except StopIteration:
        pass
    update_preview_roots(scene)
    return root


def import_project_to_scene(context, path: str | os.PathLike[str], replace: bool = False) -> dict[str, Any]:
    """Load a complete pack and populate the active Blender scene."""

    scene = context.scene
    data = read_pack(path)
    if data.get("core_project") is not None:
        _CORE_PROJECTS[scene.name] = data["core_project"]
    project = scene.halo_project
    had_existing_project = bool(project.definitions) or bool(getattr(project, "sources", ()))
    if replace:
        project.definitions.clear()
        project.sources.clear()
        # Remove only objects managed by this addon; user objects remain intact.
        for obj in list(scene.objects):
            if obj.get("halo_role") in {ROOT_ROLE, GROUP_ROLE, PRIMITIVE_ROLE}:
                bpy.data.objects.remove(obj, do_unlink=True)
    elif project.definitions:
        from .resource_store import upgrade_legacy_sources
        upgrade_legacy_sources(scene)
    source = project.sources.add()
    source.source_id = uuid.uuid4().hex
    source.source_path = data["source_path"]
    source.pack_root = data["root"]
    source.source_kind = "ZIP" if zipfile.is_zipfile(data["source_path"]) else "FOLDER"
    source.name = Path(data["source_path"]).stem if source.source_kind == "ZIP" else Path(data["source_path"]).name
    source.definition_count = len(data["definitions"])
    project.active_source_index = len(project.sources) - 1
    project.source_path = data["source_path"]
    project.pack_root = data["root"]
    if replace or not had_existing_project:
        project.manifest_json = json.dumps(data["manifest"], ensure_ascii=False, indent=2)
        project.schema_version = str(data["manifest"].get("halo_schema", "1.1.0")) if isinstance(data["manifest"], Mapping) else "1.1.0"
    imported = 0
    renamed = []
    for definition in data["definitions"]:
        requested_id = str(definition.get("id") or "minecraft:halo")
        definition_id = unique_definition_id(scene, requested_id)
        imported_definition = dict(definition)
        imported_raw = _json_copy(dict(definition.get("raw", {})))
        if definition_id != requested_id:
            imported_raw["id"] = definition_id
            relative = str(definition.get("source_path", "")).replace("\\", "/")
            if relative.startswith("assets/") and relative.lower().endswith(".json"):
                imported_definition["source_path"] = str(PurePosixPath(relative).with_name(_safe_name(definition_id.split(":", 1)[-1]) + ".json"))
            renamed.append({"from": requested_id, "to": definition_id})
        imported_definition["id"] = definition_id
        imported_definition["raw"] = imported_raw
        import_definition_to_scene(
            scene,
            imported_definition,
            replace=False,
            source_id=source.source_id,
            pack_root=data["root"],
        )
        imported += 1
    if imported == 0:
        project.validation_json = json.dumps({"errors": ["未找到 assets/*/halo_definitions/*.json"]}, ensure_ascii=False)
    data["renamed_definitions"] = renamed
    data["source_id"] = source.source_id
    return data


def _refresh_source_counts(project) -> None:
    counts: dict[str, int] = {}
    for item in project.definitions:
        source_id = str(getattr(item, "source_id", "") or "")
        if source_id:
            counts[source_id] = counts.get(source_id, 0) + 1
    for source in project.sources:
        source.definition_count = counts.get(source.source_id, 0)


def remove_definition_from_scene(scene, index: int) -> str:
    """Remove one definition and its object tree while retaining its source."""

    project = scene.halo_project
    if index < 0 or index >= len(project.definitions):
        raise IndexError("光环索引无效")
    definition_id = project.definitions[index].definition_id
    _remove_definition_objects(definition_id, scene)
    project.definitions.remove(index)
    _refresh_source_counts(project)
    if project.definitions:
        project.active_definition_index = min(index, len(project.definitions) - 1)
        project.active_definition = project.definitions[project.active_definition_index].definition_id
    else:
        project.active_definition_index = 0
        project.active_definition = ""
        project.active_uuid = ""
    return definition_id


def remove_source_from_scene(scene, index: int) -> tuple[str, list[str]]:
    """Remove a ZIP/folder source and every definition imported from it."""

    project = scene.halo_project
    if index < 0 or index >= len(project.sources):
        raise IndexError("资源包来源索引无效")
    source = project.sources[index]
    source_id = source.source_id
    source_name = source.name or Path(source.source_path).name
    removed_ids = [item.definition_id for item in project.definitions if item.source_id == source_id]
    for definition_id in removed_ids:
        _remove_definition_objects(definition_id, scene)
    for definition_index in reversed(range(len(project.definitions))):
        if project.definitions[definition_index].source_id == source_id:
            project.definitions.remove(definition_index)
    project.sources.remove(index)
    project.active_source_index = min(index, max(0, len(project.sources) - 1))
    _refresh_source_counts(project)
    if project.definitions:
        project.active_definition_index = min(project.active_definition_index, len(project.definitions) - 1)
        project.active_definition = project.definitions[project.active_definition_index].definition_id
    else:
        project.active_definition_index = 0
        project.active_definition = ""
        project.active_uuid = ""
    if project.sources:
        active_source = project.sources[project.active_source_index]
        project.source_path = active_source.source_path
        project.pack_root = active_source.pack_root
    else:
        project.source_path = ""
        project.pack_root = ""
    return source_name, removed_ids


def _raw_from_object(obj) -> dict[str, Any]:
    try:
        value = json.loads(obj.get("halo_raw_json", "{}"))
        return dict(value) if isinstance(value, Mapping) else {}
    except (TypeError, ValueError):
        return {}


def _primitive_raw(obj) -> dict[str, Any]:
    try:
        value = json.loads(obj.get("halo_primitive_raw_json", obj.get("halo_raw_json", "{}")))
        return dict(value) if isinstance(value, Mapping) else {}
    except (TypeError, ValueError):
        return {}


def _sync_primitive(obj):
    raw = _primitive_raw(obj)
    node = getattr(obj, "halo_node", None)
    if node is not None:
        raw["type"] = node.primitive_type
        if node.primitive_type == "ring":
            raw["outer_texture"] = node.texture
            raw.pop("texture", None)
            if node.inner_texture:
                raw["inner_texture"] = node.inner_texture
            else:
                raw.pop("inner_texture", None)
            raw["size"] = list(node.size)
            raw["segments"] = int(node.segments)
        elif node.primitive_type == "mesh":
            raw["model"] = node.mesh_model
            raw["texture"] = node.texture
            raw["preserve_proportions"] = bool(node.mesh_preserve_proportions)
            raw["scale"] = float(node.mesh_scale)
            if not node.mesh_preserve_proportions or bool(obj.get("halo_mesh_size_present", "size" in raw)):
                raw["size"] = list(node.mesh_size)
                obj["halo_mesh_size_present"] = True
            else:
                raw.pop("size", None)
            raw.pop("outer_texture", None)
            raw.pop("inner_texture", None)
            raw.pop("segments", None)
            raw.pop("face_camera", None)
            material = raw.get("material")
            material = dict(material) if isinstance(material, Mapping) else {}
            material["double_sided"] = bool(node.mesh_double_sided)
            effects = [dict(effect) for effect in material.get("effects", []) if isinstance(effect, Mapping)]
            old_mask = next((effect for effect in effects if str(effect.get("type", "")) == "alpha_mask"), None)
            effects = [effect for effect in effects if str(effect.get("type", "")) != "alpha_mask"]
            if node.mesh_mask_enabled:
                mask = dict(old_mask) if old_mask is not None else {}
                mask.update({
                    "type": "alpha_mask",
                    "texture": node.mesh_mask_texture,
                    "mode": node.mesh_mask_mode,
                    "threshold": float(node.mesh_mask_threshold),
                })
                effects.insert(0, mask)
            material["effects"] = effects
            raw["material"] = material
        else:
            raw["texture"] = node.texture
            raw["size"] = list(node.size)
            raw["face_camera"] = bool(node.face_camera)
    obj["halo_primitive_raw_json"] = json.dumps(raw, ensure_ascii=False, separators=(",", ":"))
    obj["halo_texture_id"] = str(raw.get("texture", raw.get("outer_texture", "")))
    obj["halo_inner_texture_id"] = str(raw.get("inner_texture", ""))
    if node is not None and node.primitive_type == "mesh":
        obj["halo_model_id"] = node.mesh_model
        obj["halo_mesh_mask_texture_id"] = node.mesh_mask_texture if node.mesh_mask_enabled else ""
    return raw


def _sync_group(obj) -> dict[str, Any]:
    raw = _raw_from_object(obj)
    node = getattr(obj, "halo_node", None)
    raw.setdefault("position", [0.0, 0.0, 0.0])
    raw.setdefault("rotation", [0.0, 0.0, 0.0])
    raw.setdefault("scale", 1.0)
    if node is not None:
        raw["position"] = [float(value) for value in node.position]
        raw["rotation"] = [float(value) for value in node.rotation]
        scale_value = float(node.scale)
        _set_group_transform(obj, raw)
        is_uniform = True
    else:
        raw["position"] = list(blender_to_mc(obj.location))
        raw["rotation"] = list(blender_rotation_to_mc_euler(obj.rotation_quaternion if obj.rotation_mode == "QUATERNION" else obj.rotation_euler))
        scale_value, is_uniform = uniform_scale(obj.scale)
    raw["scale"] = scale_value
    obj["halo_non_uniform_scale"] = not is_uniform
    if node is not None:
        node_id = str(node.node_id).strip()
        if node_id:
            raw["id"] = node_id
        else:
            raw.pop("id", None)
        node.position = raw["position"]
        node.rotation = raw["rotation"]
        node.scale = scale_value
        # These flags are group-level fields and therefore belong in JSON.
        if "glowing" in raw or not node.glowing:
            raw["glowing"] = bool(node.glowing)
        if "inherit_alpha" in raw or not node.inherit_alpha:
            raw["inherit_alpha"] = bool(node.inherit_alpha)
        if "inherit_glow" in raw or not node.inherit_glow:
            raw["inherit_glow"] = bool(node.inherit_glow)
        if node.animation_json.strip():
            try:
                raw["animation"] = json.loads(node.animation_json)
            except (TypeError, ValueError):
                pass
    primitives = [child for child in obj.children if child.get("halo_role") == PRIMITIVE_ROLE]
    primitives.sort(key=lambda child: int(child.get("halo_primitive_index", 0)))
    if obj.get("halo_original_primitives_key") == "primitive" and len(primitives) == 1:
        raw["primitive"] = _sync_primitive(primitives[0])
        raw.pop("primitives", None)
    elif primitives or "primitives" in raw:
        raw["primitives"] = [_sync_primitive(child) for child in primitives]
        raw.pop("primitive", None)
    children = [child for child in obj.children if child.get("halo_role") == GROUP_ROLE]
    children.sort(key=lambda child: child.get("halo_path", child.name))
    raw["children"] = [_sync_group(child) for child in children]
    obj["halo_raw_json"] = json.dumps(raw, ensure_ascii=False, separators=(",", ":"))
    return raw


def sync_definition_from_scene(scene, definition_id: str) -> dict[str, Any] | None:
    """Read transforms/property edits back into a definition JSON dictionary."""

    item = _project_definition(scene, definition_id)
    root_uuid = getattr(item, "root_uuid", "") if item is not None else ""
    root = next((obj for obj in scene.objects if obj.get("halo_role") == ROOT_ROLE and (
        obj.get("halo_definition_id") == definition_id or (root_uuid and obj.get("halo_uuid") == root_uuid)
    )), None)
    if root is None:
        return None
    raw = _raw_from_object(root)
    if item is not None:
        old_definition_id = str(root.get("halo_definition_id", definition_id))
        new_definition_id = str(item.definition_id or definition_id)
        if new_definition_id != old_definition_id:
            for obj in scene.objects:
                if obj.get("halo_definition_id") == old_definition_id:
                    obj["halo_definition_id"] = new_definition_id
                    if getattr(obj, "halo_node", None) is not None:
                        obj.halo_node.definition_id = new_definition_id
            root["halo_definition_id"] = new_definition_id
            item.namespace = _namespace(new_definition_id)
            if scene.halo_project.active_definition == old_definition_id:
                scene.halo_project.active_definition = new_definition_id
        raw["id"] = item.definition_id
        raw["version"] = item.schema_version or "1.1.0"
        raw["orientation_mode"] = item.orientation_mode
        if item.orientation_mode == "sync" or "sync_offset" in raw:
            raw["sync_offset"] = list(item.sync_offset)
        raw["allow_angular_momentum"] = bool(item.allow_angular_momentum)
        raw["hide_on_sleep"] = bool(item.hide_on_sleep)
        raw["display_in_invisible"] = bool(item.display_in_invisible)
        positioning = raw.get("positioning") if isinstance(raw.get("positioning"), Mapping) else {}
        positioning = dict(positioning)
        positioning["offset"] = list(item.positioning_offset)
        positioning["scale"] = float(item.positioning_scale)
        raw["positioning"] = positioning
        damping = raw.get("damping") if isinstance(raw.get("damping"), Mapping) else {}
        damping = dict(damping)
        damping.update({
            "linearFactor": float(item.damping_linear_factor),
            "angularFactor": float(item.damping_angular_factor),
            "maxLinearDistance": float(item.damping_max_linear),
            "maxAngularDegrees": float(item.damping_max_angular),
            "angularMomentumFactor": float(item.damping_angular_momentum_factor),
            "maxAngularMomentumDegrees": float(item.damping_max_angular_momentum),
        })
        raw["damping"] = damping
        for key, text in (("animation", item.animation_json), ("startup", item.startup_json), ("shutdown", item.shutdown_json)):
            if not text.strip():
                continue
            try:
                parsed = json.loads(text)
                if isinstance(parsed, Mapping):
                    raw[key] = dict(parsed)
                    item.pop(f"halo_invalid_{key}_json", None)
            except (TypeError, ValueError):
                # Validation reports malformed typed editor content without
                # destroying the last valid source object.
                item[f"halo_invalid_{key}_json"] = True
    groups = [child for child in root.children if child.get("halo_role") == GROUP_ROLE]
    groups.sort(key=lambda child: child.get("halo_path", child.name))
    raw["layers"] = [_sync_group(group) for group in groups]
    if item is not None:
        item.raw_json = json.dumps(raw, ensure_ascii=False, indent=2)
        item.animation_json = json.dumps(raw.get("animation", {}), ensure_ascii=False, indent=2)
        item.startup_json = json.dumps(raw.get("startup", {}), ensure_ascii=False, indent=2)
        item.shutdown_json = json.dumps(raw.get("shutdown", {}), ensure_ascii=False, indent=2)
        positioning = raw.get("positioning") if isinstance(raw.get("positioning"), Mapping) else {}
        item.positioning_offset = _vec(positioning.get("offset"), 3, (0, 0, 0))
        item.positioning_scale = float(positioning.get("scale", 1.0) or 1.0)
    root["halo_raw_json"] = json.dumps(raw, ensure_ascii=False, indent=2)
    return raw


def sync_all_definitions(scene) -> dict[str, dict[str, Any]]:
    result = {}
    if getattr(scene, "halo_project", None) is None:
        return result
    for item in scene.halo_project.definitions:
        raw = sync_definition_from_scene(scene, item.definition_id)
        if raw is not None:
            result[item.definition_id] = raw
    return result


def _root_positioning(scene, root):
    definition_id = root.get("halo_definition_id", "")
    item = _project_definition(scene, definition_id)
    raw = _raw_from_object(root)
    if item is not None:
        offset = list(item.positioning_offset)
        scale = float(item.positioning_scale)
        orientation = item.orientation_mode
        sync_offset = list(item.sync_offset)
    else:
        positioning = raw.get("positioning") if isinstance(raw.get("positioning"), Mapping) else {}
        offset = _vec(positioning.get("offset"), 3, (0, 0, 0))
        scale = float(positioning.get("scale", 1.0) or 1.0)
        orientation = str(raw.get("orientation_mode", "locked"))
        sync_offset = _vec(raw.get("sync_offset"), 3, (0, 0, 0))
    project = scene.halo_project
    if project.preview_space == "HALO_LOCAL":
        root.location = (0.0, 0.0, 0.0)
        root.rotation_mode = "QUATERNION"
        root.rotation_quaternion = (1.0, 0.0, 0.0, 0.0)
    else:
        root.rotation_mode = "QUATERNION"
        yaw = math.radians(float(project.head_yaw))
        pitch = math.radians(float(project.head_pitch))
        roll = math.radians(float(project.head_roll))
        forward = Vector((-math.sin(yaw) * math.cos(pitch), -math.sin(pitch), math.cos(yaw) * math.cos(pitch)))
        forward.normalize()
        world_up = Vector((0.0, 1.0, 0.0))
        if abs(forward.dot(world_up)) > 0.999:
            right = Vector((-math.cos(yaw), 0.0, -math.sin(yaw)))
        else:
            right = forward.cross(world_up).normalized()
        head_up = right.cross(forward).normalized()
        if abs(roll) > 0.001:
            old_right, old_up = right.copy(), head_up.copy()
            right = (old_right * math.cos(roll) - old_up * math.sin(roll)).normalized()
            head_up = (old_up * math.cos(roll) + old_right * math.sin(roll)).normalized()
        head_relative = right * float(offset[0]) + head_up * float(offset[1]) - forward * float(offset[2])
        anchor_mc = Vector((0.0, 1.62, 0.0))
        root.location = mc_to_blender(anchor_mc + head_relative)

        # Static first-frame AnchorFrameCalculator orientation: shortest arc
        # from definition -Y toward the head, plus LOCKED sphere spin.  FREE
        # has no spin; SYNC applies its YXZ offset to the captured LOCKED pose.
        if head_relative.length > 1e-9:
            to_head_mc = (-head_relative).normalized()
            to_head = Vector(mc_to_blender(to_head_mc))
            definition_normal = Vector(mc_to_blender((0.0, -1.0, 0.0)))
            q_look = definition_normal.rotation_difference(to_head)
            q_locked_spin = Quaternion()
            p = head_relative.normalized()
            tangent_up = head_up - p * head_up.dot(p)
            if tangent_up.length <= 1e-9:
                tangent_up = world_up - p * p.y
                if tangent_up.length <= 1e-9:
                    tangent_up = (Vector((1.0, 0.0, 0.0)).cross(p)
                                  if abs(p.x) < 0.9 else Vector((0.0, 0.0, 1.0)).cross(p))
            pole_up = tangent_up.normalized()
            z_target_mc = pole_up - to_head_mc * pole_up.dot(to_head_mc)
            if z_target_mc.length > 1e-9:
                z_target = Vector(mc_to_blender(z_target_mc.normalized()))
                z_current = q_look @ Vector(mc_to_blender((0.0, 0.0, 1.0)))
                cosine = max(-1.0, min(1.0, z_current.dot(z_target)))
                phi = math.acos(cosine)
                if z_current.cross(z_target).dot(to_head) < 0.0:
                    phi = -phi
                q_locked_spin = Quaternion(to_head, phi)
            q_locked = q_locked_spin @ q_look
            if orientation == "free":
                root.rotation_quaternion = q_look
            elif orientation == "sync":
                root.rotation_quaternion = mc_rotation_quaternion(sync_offset) @ q_locked
            else:
                root.rotation_quaternion = q_locked
        else:
            root.rotation_quaternion = (1.0, 0.0, 0.0, 0.0)
    root.scale = (scale, scale, scale)
    root["halo_preview_space"] = project.preview_space
    # Keep the non-animated anchor transform separate from the definition
    # root animation.  Minecraft composes these matrices as
    #
    #   anchor translation * anchor rotation * positioning scale
    #       * root animation translation/rotation/scale
    #
    # Blender's delta_location is added in parent/world axes, so using delta
    # transforms on this root makes MC_HEAD animation drift along world axes.
    # The frame handler uses this stable base pose to reproduce the same local
    # matrix order explicitly on every frame (and therefore never accumulates
    # animation into the anchor pose).
    root["halo_preview_base_location"] = tuple(float(value) for value in root.location)
    root["halo_preview_base_rotation"] = tuple(float(value) for value in root.rotation_quaternion)
    root["halo_preview_base_scale"] = tuple(float(value) for value in root.scale)


def update_preview_roots(scene):
    """Apply HALO_LOCAL/MC_HEAD positioning and maintain the head helper."""

    if bpy is None or getattr(scene, "halo_project", None) is None:
        return
    for root in scene.objects:
        if root.get("halo_role") == ROOT_ROLE:
            _root_positioning(scene, root)
    if scene.halo_project.preview_space == "MC_HEAD" and scene.halo_project.show_head:
        head = next((obj for obj in bpy.data.objects if obj.get("halo_role") == HEAD_ROLE and obj.get("halo_preview_scene") == scene.name), None)
        if head is None:
            bpy.ops.mesh.primitive_cube_add(size=0.5, location=mc_to_blender((0, 1.62, 0)))
            head = bpy.context.object
            head.name = "MC Player Head Preview"
            head["halo_role"] = HEAD_ROLE
            head["halo_preview_only"] = True
            head["halo_preview_scene"] = scene.name
            head.display_type = "WIRE"
            head.color = (0.15, 0.45, 0.9, 0.22)
    else:
        _remove_head_preview(scene)
    apply_definition_visibility(scene)


def apply_definition_visibility(scene) -> None:
    """Reapply editor-only visibility after import, load, or preview refresh."""

    if bpy is None or getattr(scene, "halo_project", None) is None:
        return
    visibility = {item.definition_id: bool(getattr(item, "visible", True)) for item in scene.halo_project.definitions}
    for obj in scene.objects:
        definition_id = obj.get("halo_definition_id", "")
        if definition_id not in visibility:
            continue
        hidden = not visibility[definition_id]
        obj.hide_viewport = hidden
        obj.hide_render = hidden
        try:
            obj.hide_set(hidden)
        except (RuntimeError, TypeError):
            pass


def _copy_tree_to_temp(source_root: str | None, destination: str):
    if source_root and os.path.isdir(source_root):
        for child in Path(source_root).iterdir():
            target = Path(destination) / child.name
            if child.is_dir():
                shutil.copytree(child, target, dirs_exist_ok=True)
            else:
                shutil.copy2(child, target)
    else:
        (Path(destination) / "assets").mkdir(parents=True, exist_ok=True)


def _copy_project_sources_to_temp(scene, destination: str) -> None:
    """Merge every retained ZIP/folder source into one export staging tree."""

    project = scene.halo_project
    sources = getattr(project, "sources", ())
    if not sources:
        _copy_tree_to_temp(getattr(project, "pack_root", ""), destination)
        return
    for source in sources:
        cached_root = str(source.pack_root or "")
        if cached_root and os.path.isdir(cached_root):
            _copy_tree_to_temp(cached_root, destination)
            continue
        source_path = str(source.source_path or "")
        if source.source_kind == "FOLDER" and os.path.isdir(source_path):
            _copy_tree_to_temp(source_path, destination)
        elif source.source_kind == "ZIP" and os.path.isfile(source_path) and zipfile.is_zipfile(source_path):
            _safe_extract(source_path, destination)
        else:
            raise FileNotFoundError(f"资源包来源不可用：{source.name or source_path}")


def _remove_staged_definition_files(destination: Path) -> None:
    """Prevent deleted/renamed source definitions from returning on export."""

    assets = destination / "assets"
    if not assets.is_dir():
        return
    for definition_path in assets.glob("*/halo_definitions/**/*.json"):
        if definition_path.is_file():
            definition_path.unlink()


def definition_output_relative(definition_id: str) -> str:
    """Return the canonical definition path derived only from its resource ID.

    Source packs often call every definition ``halo.json``.  Keeping that
    source filename makes a merged export overwrite definitions, so the ID is
    authoritative for both the namespace directory and JSON filename.
    """

    namespace = _namespace(definition_id)
    name = definition_id.split(":", 1)[-1]
    filename = _safe_name(name) + ".json"
    return f"assets/{namespace}/halo_definitions/{filename}"


def definition_output_collisions(definition_ids: Iterable[str]) -> dict[str, list[str]]:
    by_path: dict[str, list[str]] = {}
    for definition_id in definition_ids:
        path = definition_output_relative(str(definition_id))
        by_path.setdefault(path, []).append(str(definition_id))
    return {path: ids for path, ids in by_path.items() if len(ids) > 1}


def _definition_output_path(destination: Path, item, definition_id: str) -> Path:
    del item  # Kept in the signature for compatibility with older callers.
    candidate = destination / Path(*PurePosixPath(definition_output_relative(definition_id)).parts)
    candidate.parent.mkdir(parents=True, exist_ok=True)
    return candidate


def _write_generated_textures(destination: Path, documents):
    """Materialize packed Mesh-conversion images only in the export staging tree."""

    from .materials import split_resource_id
    from .core.texture_usage import referenced_texture_ids, texture_candidates

    referenced_paths = {path for identifier in referenced_texture_ids(documents)
                        for path in texture_candidates(identifier)}
    for image in bpy.data.images:
        if not image.get("halo_generated_texture"):
            continue
        texture_id = str(image.get("halo_texture_id") or "")
        if not texture_id:
            continue
        namespace, relative = split_resource_id(texture_id)
        if f"assets/{namespace}/{relative}" not in referenced_paths:
            continue
        target = (destination / "assets" / namespace / relative).resolve()
        try:
            target.relative_to(destination.resolve())
        except ValueError:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        old_path = image.filepath_raw
        old_format = image.file_format
        try:
            image.filepath_raw = str(target)
            image.file_format = "PNG"
            image.save()
        finally:
            image.filepath_raw = old_path
            image.file_format = old_format


def _prune_unused_staged_textures(destination: Path, documents) -> list[str]:
    """Remove unused texture families from this export's private staging tree."""
    from .core.texture_usage import unused_texture_files

    root = destination.resolve()
    files = {path.relative_to(root).as_posix(): path for path in root.rglob("*") if path.is_file()}
    unused = sorted(unused_texture_files(files, documents))
    for relative in unused:
        path = files[relative]
        path.resolve().relative_to(root)
        path.unlink()
    # Also remove empty former namespaces from folder exports. This never
    # removes directories that still contain definitions or unknown files.
    assets = root / "assets"
    for path in sorted(assets.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if path.is_dir() and not path.is_symlink() and not any(path.iterdir()):
            path.resolve().relative_to(root)
            path.rmdir()
    return unused


def export_pack_from_scene(scene, target_path: str | os.PathLike[str], zip_output: bool | None = None, overwrite: bool = False) -> str:
    """Export the current scene into a folder or ZIP using an atomic temp tree."""

    from .animation_text import apply_pending_animation_texts
    from .resource_store import ensure_resources
    apply_pending_animation_texts(scene, strict=True, refresh=False)
    ensure_resources(scene, rebind=False)

    hierarchy_errors = []
    for obj in scene.objects:
        role = obj.get("halo_role")
        if role == GROUP_ROLE:
            if obj.parent is None or obj.parent.get("halo_role") not in {ROOT_ROLE, GROUP_ROLE}:
                hierarchy_errors.append(f"{obj.name}: 非法组父级")
            elif obj.parent.get("halo_definition_id") != obj.get("halo_definition_id"):
                hierarchy_errors.append(f"{obj.name}: 跨光环父级")
            if not uniform_scale(obj.scale)[1]:
                hierarchy_errors.append(f"{obj.name}: 非统一缩放")
        elif role == PRIMITIVE_ROLE:
            if obj.parent is None or obj.parent.get("halo_role") != GROUP_ROLE:
                hierarchy_errors.append(f"{obj.name}: 图元必须位于组下")
            elif obj.parent.get("halo_definition_id") != obj.get("halo_definition_id"):
                hierarchy_errors.append(f"{obj.name}: 跨光环父级")
    if hierarchy_errors:
        raise ValueError("导出前验证失败：" + "；".join(hierarchy_errors[:8]))
    output_collisions = definition_output_collisions(
        item.definition_id for item in scene.halo_project.definitions
    )
    if output_collisions:
        details = "；".join(
            f"{path}: {', '.join(ids)}" for path, ids in sorted(output_collisions.items())
        )
        raise ValueError(f"光环定义文件名冲突，请先修改光环 ID：{details}")
    target = Path(target_path).expanduser().resolve()
    source_paths = {
        Path(value).expanduser().resolve()
        for value in [
            scene.halo_project.source_path,
            *(entry.source_path for entry in getattr(scene.halo_project, "sources", ())),
        ]
        if value
    }
    if target in source_paths:
        raise ValueError("导出路径不能覆盖导入源；请选择新的路径")
    if zip_output is None:
        zip_output = target.suffix.lower() == ".zip"
    raw_definitions = sync_all_definitions(scene)
    target.parent.mkdir(parents=True, exist_ok=True)
    # Keep staging on the target volume so the final rename is atomic.
    temporary = Path(tempfile.mkdtemp(prefix=".halo_pack_export_", dir=str(target.parent)))
    try:
        _copy_project_sources_to_temp(scene, str(temporary))
        _remove_staged_definition_files(temporary)
        manifest = _json_copy(DEFAULT_MANIFEST)
        try:
            parsed_manifest = json.loads(scene.halo_project.manifest_json or "{}")
            if isinstance(parsed_manifest, Mapping):
                manifest = dict(parsed_manifest)
        except (TypeError, ValueError):
            pass
        manifest = _complete_manifest(manifest)
        (temporary / "pack.mcmeta").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        export_records = []
        for item in scene.halo_project.definitions:
            raw = raw_definitions.get(item.definition_id)
            if raw is None:
                try:
                    raw = json.loads(item.raw_json)
                except (TypeError, ValueError):
                    continue
            export_records.append((item, raw))
        exported_documents = [raw for _item, raw in export_records]
        # Generated images still carry the editing project's original ID.  Put
        # them into staging first, then lower-case/migrate both files and JSON
        # references together.  Existing .blend files therefore repair
        # themselves on their next export without mutating the editing cache.
        _write_generated_textures(temporary, exported_documents)
        normalize_staged_resources(temporary, exported_documents)
        for item, raw in export_records:
            path = _definition_output_path(temporary, item, item.definition_id)
            path.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        # The JSON actually written above is authoritative, not stale Images,
        # cached source definitions, visible-object selection or namespaces.
        _prune_unused_staged_textures(temporary, exported_documents)
        if zip_output:
            target.parent.mkdir(parents=True, exist_ok=True)
            temp_zip = Path(tempfile.mktemp(prefix="halo_pack_", suffix=".zip", dir=str(target.parent)))
            try:
                with zipfile.ZipFile(temp_zip, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                    for file_path in sorted(temporary.rglob("*")):
                        if file_path.is_file():
                            archive.write(file_path, file_path.relative_to(temporary).as_posix())
                if target.exists() and not overwrite:
                    raise FileExistsError(str(target))
                os.replace(temp_zip, target)
            finally:
                if temp_zip.exists():
                    temp_zip.unlink()
        else:
            if target.exists() and not overwrite:
                raise FileExistsError(str(target))
            backup = None
            try:
                if target.exists():
                    backup = target.parent / f".{target.name}.halo_backup_{uuid.uuid4().hex}"
                    os.replace(target, backup)
                os.replace(temporary, target)
            except Exception:
                if backup is not None and backup.exists() and not target.exists():
                    os.replace(backup, target)
                raise
            else:
                if backup is not None and backup.exists():
                    if backup.is_dir():
                        shutil.rmtree(backup)
                    else:
                        backup.unlink()
        scene.halo_project.output_path = str(target)
        return str(target)
    finally:
        shutil.rmtree(temporary, ignore_errors=True)


def object_by_uuid(value: str):
    if not value:
        return None
    return next((obj for obj in bpy.data.objects if obj.get("halo_uuid") == value), None)


def is_descendant(candidate, ancestor) -> bool:
    current = candidate
    while current is not None:
        if current == ancestor:
            return True
        current = current.parent
    return False


def reparent_object(obj, new_parent, preserve_world: bool = True):
    """Reparent a group while preventing cycles and primitive-as-parent trees."""

    if obj is None or new_parent is None:
        raise ValueError("需要选择部件和目标父级")
    if obj.get("halo_role") != GROUP_ROLE or new_parent.get("halo_role") not in {GROUP_ROLE, ROOT_ROLE}:
        raise ValueError("只有组可以移动到光环根或另一个组下")
    if obj.get("halo_definition_id") != new_parent.get("halo_definition_id"):
        raise ValueError("不能把组移动到另一个光环定义下")
    if obj == new_parent or is_descendant(new_parent, obj):
        raise ValueError("不能将部件移动到自身或其子级下")
    world = obj.matrix_world.copy() if preserve_world else None
    obj.parent = new_parent
    if preserve_world and world is not None:
        obj.matrix_world = world
    obj["halo_parent_uuid"] = new_parent.get("halo_uuid", "")
    node = getattr(obj, "halo_node", None)
    if node is not None:
        obj["halo_property_update_guard"] = True
        try:
            node.position = blender_to_mc(obj.location)
            rotation = obj.rotation_quaternion if obj.rotation_mode == "QUATERNION" else obj.rotation_euler
            node.rotation = blender_rotation_to_mc_euler(rotation)
            node.scale = uniform_scale(obj.scale)[0]
            node.parent_uuid = obj["halo_parent_uuid"]
        finally:
            obj.pop("halo_property_update_guard", None)
    _lock_managed_transform(obj)
    return obj


def remove_object_tree(obj):
    """Delete one managed subtree; callers should validate the root first."""

    if obj is None or obj.get("halo_role") not in {GROUP_ROLE, PRIMITIVE_ROLE, ROOT_ROLE}:
        raise ValueError("请选择 Halo 部件")
    for child in list(obj.children):
        remove_object_tree(child)
    bpy.data.objects.remove(obj, do_unlink=True)


__all__ = [
    "COLLECTION_NAME",
    "normalise_definition",
    "read_pack",
    "unique_definition_id",
    "definition_pack_root",
    "ensure_source_roots",
    "import_project_to_scene",
    "import_definition_to_scene",
    "remove_definition_from_scene",
    "remove_source_from_scene",
    "sync_definition_from_scene",
    "sync_all_definitions",
    "reset_primitive_transform",
    "enforce_managed_transform_locks",
    "update_preview_roots",
    "apply_definition_visibility",
    "export_pack_from_scene",
    "object_by_uuid",
    "reparent_object",
    "remove_object_tree",
]
