"""User-facing Blender operators for importing, editing and exporting packs."""

from __future__ import annotations

import copy
import json
import os
import tempfile
import uuid
from pathlib import Path
from typing import Any, Mapping

try:
    import bpy
    from bpy.props import BoolProperty, EnumProperty, FloatProperty, FloatVectorProperty, IntProperty, StringProperty
    from bpy_extras.io_utils import ExportHelper, ImportHelper
except ImportError:  # pragma: no cover - Blender-only module
    bpy = None
    ImportHelper = ExportHelper = object

from . import blender_scene
from .blender_scene import (
    GROUP_ROLE,
    HEAD_ROLE,
    PRIMITIVE_ROLE,
    ROOT_ROLE,
    export_pack_from_scene,
    import_definition_to_scene,
    import_project_to_scene,
    object_by_uuid,
    reparent_object,
    remove_object_tree,
    sync_all_definitions,
    sync_definition_from_scene,
    update_preview_roots,
)
from .geometry import billboard_mesh, mc_rotation_quaternion, ring_mesh
from .materials import (
    assign_primitive_materials,
    copy_texture_with_sidecars,
    split_resource_id,
)
from .properties import EASING_ITEMS, TRANSITION_DEFAULT_GROUP


def _active_object(context):
    obj = context.active_object if context else None
    return obj if obj and obj.get("halo_role") in {ROOT_ROLE, GROUP_ROLE, PRIMITIVE_ROLE} else None


def _active_root(context):
    obj = _active_object(context)
    while obj is not None and obj.get("halo_role") != ROOT_ROLE:
        obj = obj.parent
    if obj is not None:
        return obj
    active_id = getattr(context.scene.halo_project, "active_definition", "")
    return next((item for item in bpy.data.objects if item.get("halo_role") == ROOT_ROLE and item.get("halo_definition_id") == active_id), None)


def _select_object(context, obj):
    if obj is None:
        return
    for item in context.selected_objects:
        item.select_set(False)
    obj.select_set(True)
    context.view_layer.objects.active = obj
    if obj.get("halo_definition_id"):
        context.scene.halo_project.active_definition = obj.get("halo_definition_id")
    context.scene.halo_project.active_uuid = obj.get("halo_uuid", "")


_REPARENT_TARGET_ITEMS: list[tuple[str, str, str]] = []
_PRIMITIVE_MOVE_TARGET_ITEMS: list[tuple[str, str, str]] = []


def _group_display_name(obj) -> str:
    """Return a readable, stable label for a managed group or definition root."""

    if obj is None:
        return "无"
    if obj.get("halo_role") == ROOT_ROLE:
        definition_id = obj.get("halo_definition_id", "")
        return f"光环根（顶层） · {definition_id}" if definition_id else "光环根（顶层）"
    node = getattr(obj, "halo_node", None)
    node_id = str(getattr(node, "node_id", "") or "").strip()
    return node_id or obj.name


def _reparent_target_items(_self, context):
    """List legal parents in the active definition without exposing UUID entry."""

    _REPARENT_TARGET_ITEMS.clear()
    obj = _active_object(context)
    if obj is None or obj.get("halo_role") != GROUP_ROLE or bpy is None:
        return _REPARENT_TARGET_ITEMS

    definition_id = obj.get("halo_definition_id", "")
    candidates = [
        candidate for candidate in bpy.data.objects
        if candidate.get("halo_definition_id") == definition_id
        and candidate.get("halo_role") in {ROOT_ROLE, GROUP_ROLE}
    ]

    def is_in_active_subtree(candidate):
        current = candidate
        while current is not None:
            if current == obj:
                return True
            current = current.parent
        return False

    roots = [candidate for candidate in candidates if candidate.get("halo_role") == ROOT_ROLE]
    groups = [candidate for candidate in candidates if candidate.get("halo_role") == GROUP_ROLE and not is_in_active_subtree(candidate)]

    def group_sort_key(candidate):
        path = []
        current = candidate
        while current is not None and current.get("halo_role") == GROUP_ROLE:
            path.append(_group_display_name(current).casefold())
            current = current.parent
        return tuple(reversed(path))

    for candidate in roots + sorted(groups, key=group_sort_key):
        depth = 0
        current = candidate.parent
        while current is not None and current.get("halo_role") == GROUP_ROLE:
            depth += 1
            current = current.parent
        prefix = "    " * depth + ("↳ " if depth else "")
        label = _group_display_name(candidate) if candidate.get("halo_role") == ROOT_ROLE else prefix + _group_display_name(candidate)
        uuid_value = str(candidate.get("halo_uuid", ""))
        if uuid_value:
            _REPARENT_TARGET_ITEMS.append((uuid_value, label, f"移动到 {label.strip()} 下"))
    return _REPARENT_TARGET_ITEMS


def _primitive_move_target_items(_self, context):
    """List legal containers for a new wrapper group around a primitive."""

    _PRIMITIVE_MOVE_TARGET_ITEMS.clear()
    obj = _active_object(context)
    if obj is None or obj.get("halo_role") != PRIMITIVE_ROLE or bpy is None:
        return _PRIMITIVE_MOVE_TARGET_ITEMS
    source_group = obj.parent
    definition_id = obj.get("halo_definition_id", "")
    candidates = [
        candidate for candidate in bpy.data.objects
        if candidate.get("halo_definition_id") == definition_id
        and candidate.get("halo_role") in {ROOT_ROLE, GROUP_ROLE}
        and candidate != source_group
    ]

    def sort_key(candidate):
        if candidate.get("halo_role") == ROOT_ROLE:
            return ("",)
        path = []
        current = candidate
        while current is not None and current.get("halo_role") == GROUP_ROLE:
            path.append(_group_display_name(current).casefold())
            current = current.parent
        return tuple(reversed(path))

    for candidate in sorted(candidates, key=sort_key):
        depth = 0
        current = candidate.parent
        while current is not None and current.get("halo_role") == GROUP_ROLE:
            depth += 1
            current = current.parent
        prefix = "    " * depth + ("↳ " if depth else "")
        label = _group_display_name(candidate) if candidate.get("halo_role") == ROOT_ROLE else prefix + _group_display_name(candidate)
        uuid_value = str(candidate.get("halo_uuid", ""))
        if uuid_value:
            _PRIMITIVE_MOVE_TARGET_ITEMS.append((uuid_value, label, f"在 {label.strip()} 下创建属性副本组"))
    return _PRIMITIVE_MOVE_TARGET_ITEMS


def _unique_group_id(definition_id: str, base: str) -> str:
    base = str(base or "group").strip() or "group"
    used = {
        str(getattr(getattr(obj, "halo_node", None), "node_id", "") or "")
        for obj in bpy.data.objects
        if obj.get("halo_role") == GROUP_ROLE and obj.get("halo_definition_id") == definition_id
    }
    if base not in used:
        return base
    index = 2
    while f"{base}_{index}" in used:
        index += 1
    return f"{base}_{index}"


def _remap_copied_group_ids(raw: dict[str, Any], definition_id: str) -> list[tuple[str, str]]:
    """Give every authored group in a copied subtree an independent ID."""

    used = {
        str(getattr(getattr(obj, "halo_node", None), "node_id", "") or "")
        for obj in bpy.data.objects
        if obj.get("halo_role") == GROUP_ROLE and obj.get("halo_definition_id") == definition_id
    }
    remapped: list[tuple[str, str]] = []

    def unique(base: str) -> str:
        candidate = base
        index = 2
        while candidate in used:
            candidate = f"{base}_{index}"
            index += 1
        used.add(candidate)
        return candidate

    def visit(group: dict[str, Any], fallback: str):
        old_id = str(group.get("id", "") or "").strip()
        if old_id:
            new_id = unique(old_id + "_copy")
            group["id"] = new_id
            remapped.append((old_id, new_id))
        elif fallback:
            group["id"] = unique(fallback)
        children = group.get("children")
        if isinstance(children, list):
            for index, child in enumerate(children):
                if isinstance(child, dict):
                    visit(child, f"group_copy_{index + 1}")

    visit(raw, "group_copy")
    return remapped


def _copy_transition_overrides(scene, definition_id: str, id_pairs: list[tuple[str, str]]):
    """Copy startup/shutdown id_overrides when copied groups receive new IDs."""

    item = next((entry for entry in scene.halo_project.definitions if entry.definition_id == definition_id), None)
    if item is None:
        return
    for property_name in ("startup_json", "shutdown_json"):
        try:
            document = json.loads(getattr(item, property_name) or "{}")
        except (TypeError, ValueError):
            continue
        if not isinstance(document, dict) or not isinstance(document.get("id_overrides"), dict):
            continue
        overrides = document["id_overrides"]
        changed = False
        for old_id, new_id in id_pairs:
            if old_id in overrides and new_id not in overrides:
                overrides[new_id] = copy.deepcopy(overrides[old_id])
                changed = True
        if changed:
            setattr(item, property_name, json.dumps(document, ensure_ascii=False, indent=2))


_GROUP_STANDARD_KEYS = {
    "id", "position", "rotation", "scale", "animation",
    "glowing", "inherit_alpha", "inherit_glow",
    "children", "primitives", "primitive",
}


def _selected_group_property_copy(
    source_raw: Mapping[str, Any],
    new_group_id: str,
    *,
    carry_position: bool,
    carry_rotation: bool,
    carry_scale: bool,
    carry_animation: bool,
    carry_render: bool,
    carry_extra: bool,
) -> dict[str, Any]:
    """Copy selected group properties while always excluding tree contents."""

    if carry_extra:
        raw = copy.deepcopy(dict(source_raw))
    else:
        raw = {
            key: copy.deepcopy(value)
            for key, value in source_raw.items()
            if key in _GROUP_STANDARD_KEYS
        }
    for key in ("children", "primitives", "primitive"):
        raw.pop(key, None)
    raw["id"] = new_group_id
    if not carry_position:
        raw["position"] = [0.0, 0.0, 0.0]
    if not carry_rotation:
        raw["rotation"] = [0.0, 0.0, 0.0]
    if not carry_scale:
        raw["scale"] = 1.0
    if not carry_animation:
        raw.pop("animation", None)
    if not carry_render:
        raw.pop("glowing", None)
        raw.pop("inherit_alpha", None)
        raw.pop("inherit_glow", None)
    return raw


def _apply_group_property_selection(
    obj,
    *,
    carry_position: bool,
    carry_rotation: bool,
    carry_scale: bool,
    carry_animation: bool,
    carry_render: bool,
    carry_extra: bool,
):
    """Reset unchecked carried fields on an already reparented group."""

    node = getattr(obj, "halo_node", None)
    if node is None:
        return
    obj["halo_property_update_guard"] = True
    try:
        if not carry_position:
            node.position = (0.0, 0.0, 0.0)
        if not carry_rotation:
            node.rotation = (0.0, 0.0, 0.0)
        if not carry_scale:
            node.scale = 1.0
        if not carry_animation:
            node.animation_json = "{}"
            obj["halo_animation_json"] = "{}"
        if not carry_render:
            node.glowing = True
            node.inherit_alpha = True
            node.inherit_glow = True
    finally:
        obj.pop("halo_property_update_guard", None)
    raw = blender_scene._sync_group(obj)
    if not carry_extra:
        raw = {key: value for key, value in raw.items() if key in _GROUP_STANDARD_KEYS}
        obj["halo_raw_json"] = json.dumps(raw, ensure_ascii=False, separators=(",", ":"))
        node.raw_json = json.dumps(raw, ensure_ascii=False, indent=2)


def _draw_carry_options(layout, operator, *, include_transition: bool):
    box = layout.box()
    box.label(text="移动时携带的组属性", icon="PROPERTIES")
    grid = box.grid_flow(row_major=True, columns=2, even_columns=True, align=True)
    grid.prop(operator, "carry_position")
    grid.prop(operator, "carry_rotation")
    grid.prop(operator, "carry_scale")
    grid.prop(operator, "carry_animation")
    grid.prop(operator, "carry_render")
    grid.prop(operator, "carry_extra")
    if include_transition:
        grid.prop(operator, "carry_transition")


def _animation_owner(context):
    obj = _active_object(context)
    if obj is not None and obj.get("halo_role") == PRIMITIVE_ROLE and obj.parent is not None:
        obj = obj.parent
    if obj is not None and obj.get("halo_role") == GROUP_ROLE:
        return obj.halo_node
    active_id = context.scene.halo_project.active_definition
    return next((item for item in context.scene.halo_project.definitions if item.definition_id == active_id), None)


def _animation_terms(animation, channel, create=False):
    if "." not in channel:
        if create:
            animation.setdefault(channel, [])
        return animation.get(channel) if isinstance(animation.get(channel), list) else None
    block, axis = channel.split(".", 1)
    if create:
        animation.setdefault(block, {})
        if not isinstance(animation[block], dict):
            animation[block] = {}
        animation[block].setdefault(axis, [])
    parent = animation.get(block)
    if not isinstance(parent, dict):
        return None
    return parent.get(axis) if isinstance(parent.get(axis), list) else None


def _transition_definition(context):
    project = context.scene.halo_project
    return next((item for item in project.definitions if item.definition_id == project.active_definition), None)


def _transition_document(context):
    """Load the selected startup/shutdown document without hiding JSON errors."""

    item = _transition_definition(context)
    if item is None:
        raise ValueError("请先选择光环定义")
    target = context.scene.halo_project.transition_target
    payload = item.startup_json if target == "startup" else item.shutdown_json
    try:
        document = json.loads(payload or "{}")
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{target} JSON 无法解析：{exc}") from exc
    if not isinstance(document, dict):
        raise ValueError(f"{target} JSON 根节点必须是对象")
    return item, target, document


def _transition_segments(context, *, create=False):
    """Return the authored segment list for the selected default/group track."""

    item, target, document = _transition_document(context)
    group_id = str(context.scene.halo_project.transition_group_id or TRANSITION_DEFAULT_GROUP)
    if group_id == TRANSITION_DEFAULT_GROUP:
        segments = document.get("segments")
        if not isinstance(segments, list):
            if not create:
                segments = []
            else:
                segments = []
                document["segments"] = segments
        return item, target, document, segments, True

    overrides = document.get("id_overrides")
    if not isinstance(overrides, dict):
        if not create:
            return item, target, document, [], False
        overrides = {}
        document["id_overrides"] = overrides
    entry = overrides.get(group_id)
    exists = entry is not None
    if isinstance(entry, list):
        segments = entry
    elif isinstance(entry, dict) and isinstance(entry.get("segments"), list):
        segments = entry["segments"]
    elif create:
        entry = dict(entry) if isinstance(entry, dict) else {}
        segments = []
        entry["segments"] = segments
        overrides[group_id] = entry
        exists = True
    else:
        segments = []
    return item, target, document, segments, exists


def _store_transition_document(item, target: str, document: Mapping[str, Any]):
    payload = json.dumps(dict(document), ensure_ascii=False, indent=2)
    if target == "startup":
        item.startup_json = payload
    else:
        item.shutdown_json = payload


def _transition_channel_entry(segment: Mapping[str, Any], channel: str):
    key = channel
    if channel == "alpha" and not isinstance(segment.get("alpha"), Mapping) and isinstance(segment.get("opacity"), Mapping):
        key = "opacity"
    value = segment.get(key)
    return key, value if isinstance(value, Mapping) else None


def _transition_identity(channel: str):
    if channel == "scale":
        return (1.0, 1.0, 1.0)
    if channel == "alpha":
        return (1.0,)
    return (0.0, 0.0, 0.0)


def _transition_boundary_errors(segments, target: str):
    """Return authored-boundary violations using the Java parser's rule."""

    errors = set()
    endpoint = "from" if target == "startup" else "to"
    for channel in ("offset", "scale", "alpha", "rotation"):
        active = []
        for index, segment in enumerate(segments):
            if isinstance(segment, Mapping):
                _key, prop = _transition_channel_entry(segment, channel)
                if prop is not None:
                    active.append((index, prop))
        if not active:
            continue
        index, prop = active[0 if target == "startup" else -1]
        if prop.get(endpoint) is None:
            errors.add((channel, index, endpoint))
    return errors


def _transition_vector3(value, default=(0.0, 0.0, 0.0)):
    if isinstance(value, (list, tuple)):
        values = [float(item) for item in value[:3]]
    elif value is None:
        values = []
    else:
        values = [float(value)]
    while len(values) < 3:
        values.append(float(default[len(values)]))
    return tuple(values)


def _refresh_transition_preview(context):
    try:
        from .handlers import update_animation
        update_animation(context.scene)
    except Exception:
        # Editing remains authoritative even when a malformed unrelated node
        # prevents a particular preview frame from evaluating.
        pass


def _set_node_mesh(obj):
    node = getattr(obj, "halo_node", None)
    if node is None or obj.get("halo_role") != PRIMITIVE_ROLE:
        return False
    old_mesh = obj.data
    if node.primitive_type == "ring":
        obj.data = ring_mesh(obj.name, node.size, node.segments, bool(node.inner_texture))
    else:
        obj.data = billboard_mesh(obj.name, node.size)
    if old_mesh and old_mesh.users == 0:
        bpy.data.meshes.remove(old_mesh)
    blender_scene.reset_primitive_transform(obj)
    pack_root = getattr(bpy.context.scene.halo_project, "pack_root", "")
    group_node = getattr(obj.parent, "halo_node", None) if obj.parent is not None else None
    glowing = bool(group_node.glowing) if group_node is not None else True
    assign_primitive_materials(obj, node.texture, node.inner_texture or None, pack_root, glowing=glowing)
    obj["halo_face_camera"] = bool(node.face_camera)
    obj["halo_raw_json"] = obj.get("halo_primitive_raw_json", obj.get("halo_raw_json", "{}"))
    return True


def _duplicate_node_from_json(context, obj):
    """Create a schema-correct sibling copy with fresh UUIDs throughout."""

    parent = obj.parent
    if parent is None or parent.get("halo_role") not in {ROOT_ROLE, GROUP_ROLE}:
        raise ValueError("所选部件没有有效的当前父级")
    collection = obj.users_collection[0] if obj.users_collection else blender_scene._ensure_collection(context.scene)
    definition_id = obj.get("halo_definition_id", "")
    path_token = uuid.uuid4().hex[:12]
    if obj.get("halo_role") == GROUP_ROLE:
        raw = copy.deepcopy(blender_scene._sync_group(obj))
        id_pairs = _remap_copied_group_ids(raw, definition_id)
        clone = blender_scene._make_group(collection, parent, raw, definition_id, f"manual/copy/{path_token}")
        _copy_transition_overrides(context.scene, definition_id, id_pairs)
    elif obj.get("halo_role") == PRIMITIVE_ROLE:
        raw = copy.deepcopy(blender_scene._sync_primitive(obj))
        parent_node = getattr(parent, "halo_node", None)
        glowing = bool(parent_node.glowing) if parent_node is not None else True
        clone = blender_scene._make_primitive(collection, parent, raw, definition_id, f"manual/primitive/{path_token}", glowing)
        sibling_indices = [
            int(child.get("halo_primitive_index", 0))
            for child in parent.children if child != clone and child.get("halo_role") == PRIMITIVE_ROLE
        ]
        clone["halo_primitive_index"] = max(sibling_indices, default=-1) + 1
    else:
        raise ValueError("只能复制部件组或图元")
    sync_definition_from_scene(context.scene, definition_id)
    return clone


def validate_scene(scene) -> dict[str, list[str]]:
    """Validate the Blender tree without rejecting unknown JSON fields."""

    errors: list[str] = []
    warnings: list[str] = []
    definitions = getattr(scene.halo_project, "definitions", ())
    seen_ids = set()
    for item in definitions:
        if item.definition_id in seen_ids:
            errors.append(f"重复的光环 ID: {item.definition_id}")
        seen_ids.add(item.definition_id)
        root = next((obj for obj in bpy.data.objects if obj.get("halo_role") == ROOT_ROLE and obj.get("halo_definition_id") == item.definition_id), None)
        if root is None:
            errors.append(f"缺少光环根对象: {item.definition_id}")
            continue
        try:
            raw = sync_definition_from_scene(scene, item.definition_id)
        except Exception as exc:
            errors.append(f"{item.definition_id}: 无法从场景同步 JSON ({exc})")
            continue
        if not raw or not isinstance(raw.get("layers"), list):
            errors.append(f"{item.definition_id}: layers 必须是数组")
        if item.orientation_mode not in {"locked", "free", "sync"}:
            errors.append(f"{item.definition_id}: 无效 orientation_mode")
        for obj in bpy.data.objects:
            if obj.get("halo_definition_id") != item.definition_id:
                continue
            if obj.get("halo_role") == GROUP_ROLE:
                if obj.parent is None or obj.parent.get("halo_role") not in {ROOT_ROLE, GROUP_ROLE}:
                    errors.append(f"{obj.name}: 组只能成为光环根或另一组的子级")
                elif obj.parent.get("halo_definition_id") != item.definition_id:
                    errors.append(f"{obj.name}: 父级属于另一个光环定义")
                if obj.get("halo_non_uniform_scale"):
                    errors.append(f"{obj.name}: JSON 组缩放必须是统一值")
                if not obj.get("halo_uuid"):
                    errors.append(f"{obj.name}: 缺少稳定 UUID")
            elif obj.get("halo_role") == PRIMITIVE_ROLE:
                if obj.parent is None or obj.parent.get("halo_role") != GROUP_ROLE:
                    errors.append(f"{obj.name}: 图元必须直接位于部件组下")
                elif obj.parent.get("halo_definition_id") != item.definition_id:
                    errors.append(f"{obj.name}: 父级属于另一个光环定义")
                node = getattr(obj, "halo_node", None)
                if node is None:
                    errors.append(f"{obj.name}: 缺少图元属性")
                    continue
                if node.primitive_type == "ring" and node.segments < 3:
                    errors.append(f"{obj.name}: ring segments 至少为 3")
                if node.size[0] <= 0.0 or node.size[1] <= 0.0:
                    warnings.append(f"{obj.name}: 图元尺寸包含非正值")
                if not node.texture:
                    warnings.append(f"{obj.name}: 缺少纹理资源 ID")
                for material in getattr(obj.data, "materials", ()):
                    if material and material.get("halo_missing_texture"):
                        warnings.append(f"{obj.name}: 纹理不存在，当前使用占位材质")
        for key, label in (("animation", "常驻动画"), ("startup", "启动动画"), ("shutdown", "关闭动画")):
            if item.get(f"halo_invalid_{key}_json"):
                errors.append(f"{item.definition_id}: {label} JSON 无法解析")
    result = {"errors": errors, "warnings": warnings}
    scene.halo_project.validation_json = json.dumps(result, ensure_ascii=False, indent=2)
    return result


if bpy is not None:

    class HALO_OT_new_project(bpy.types.Operator):
        bl_idname = "halo.new_project"
        bl_label = "新建光环资源包"
        bl_description = "清空 Halo 编辑器场景数据并建立一个空白资源包项目"
        bl_options = {"REGISTER", "UNDO"}

        with_default_halo: BoolProperty(name="创建示例光环", default=True)

        def execute(self, context):
            project = context.scene.halo_project
            for obj in list(bpy.data.objects):
                if obj.get("halo_role") in {ROOT_ROLE, GROUP_ROLE, PRIMITIVE_ROLE, HEAD_ROLE}:
                    bpy.data.objects.remove(obj, do_unlink=True)
            project.definitions.clear()
            project.source_path = ""
            project.pack_root = ""
            project.manifest_json = json.dumps(blender_scene.DEFAULT_MANIFEST, ensure_ascii=False, indent=2)
            project.active_definition = ""
            if self.with_default_halo:
                raw = {
                    "id": "minecraft:halo",
                    "version": "1.0.10",
                    "orientation_mode": "locked",
                    "layers": [{
                        "id": "halo",
                        "position": [0.0, 0.0, 0.0],
                        "rotation": [0.0, 0.0, 0.0],
                        "scale": 1.0,
                        "primitive": {"type": "billboard", "texture": "minecraft:textures/halo/example.png", "size": [1.0, 1.0]},
                    }],
                    "animation": {},
                    "positioning": {"offset": [0.0, 0.0, 0.0], "scale": 1.0},
                }
                import_definition_to_scene(context.scene, {"id": raw["id"], "raw": raw}, replace=False)
            self.report({"INFO"}, "已建立空白 Halo 资源包项目")
            return {"FINISHED"}


    class HALO_OT_new_definition(bpy.types.Operator):
        bl_idname = "halo.new_definition"
        bl_label = "新建光环"
        bl_options = {"REGISTER", "UNDO"}

        definition_id: StringProperty(name="光环 ID", default="minecraft:new_halo")

        def invoke(self, context, event):
            return context.window_manager.invoke_props_dialog(self)

        def execute(self, context):
            definition_id = self.definition_id.strip() or "minecraft:new_halo"
            if ":" not in definition_id:
                definition_id = "minecraft:" + definition_id
            raw = {
                "id": definition_id,
                "version": "1.0.10",
                "orientation_mode": "locked",
                "layers": [],
                "animation": {},
                "positioning": {"offset": [0.0, 0.0, 0.0], "scale": 1.0},
            }
            import_definition_to_scene(context.scene, {"id": definition_id, "raw": raw}, replace=False)
            self.report({"INFO"}, f"已创建 {definition_id}")
            return {"FINISHED"}


    class HALO_OT_import_pack(bpy.types.Operator, ImportHelper):
        bl_idname = "halo.import_pack"
        bl_label = "导入 Halo 资源包"
        bl_options = {"REGISTER", "UNDO"}
        filename_ext = ".zip"
        filter_glob: StringProperty(default="*.zip;*.mcpack;*.json", options={"HIDDEN"})
        replace_scene: BoolProperty(name="替换当前项目", default=True)

        def execute(self, context):
            try:
                import_project_to_scene(context, self.filepath, self.replace_scene)
            except Exception as exc:
                self.report({"ERROR"}, f"导入失败: {exc}")
                return {"CANCELLED"}
            self.report({"INFO"}, f"已导入 Halo 资源包: {self.filepath}")
            return {"FINISHED"}


    class HALO_OT_import_folder(bpy.types.Operator):
        bl_idname = "halo.import_folder"
        bl_label = "导入资源包文件夹"
        bl_options = {"REGISTER", "UNDO"}

        directory: StringProperty(name="资源包文件夹", subtype="DIR_PATH")
        replace_scene: BoolProperty(name="替换当前项目", default=True)

        def invoke(self, context, event):
            context.window_manager.fileselect_add(self)
            return {"RUNNING_MODAL"}

        def execute(self, context):
            try:
                import_project_to_scene(context, self.directory, self.replace_scene)
            except Exception as exc:
                self.report({"ERROR"}, f"导入失败: {exc}")
                return {"CANCELLED"}
            self.report({"INFO"}, "已导入资源包文件夹")
            return {"FINISHED"}


    class HALO_OT_export_pack(bpy.types.Operator):
        bl_idname = "halo.export_pack"
        bl_label = "导出资源包文件夹"
        bl_options = {"REGISTER"}

        filepath: StringProperty(name="导出文件夹", subtype="DIR_PATH")
        overwrite: BoolProperty(name="允许覆盖", default=False)

        def invoke(self, context, event):
            self.filepath = context.scene.halo_project.output_path or ""
            context.window_manager.fileselect_add(self)
            return {"RUNNING_MODAL"}

        def execute(self, context):
            try:
                export_pack_from_scene(context.scene, self.filepath, zip_output=False, overwrite=self.overwrite)
            except Exception as exc:
                self.report({"ERROR"}, f"导出失败: {exc}")
                return {"CANCELLED"}
            self.report({"INFO"}, "资源包文件夹导出完成")
            return {"FINISHED"}


    class HALO_OT_export_zip(bpy.types.Operator, ExportHelper):
        bl_idname = "halo.export_zip"
        bl_label = "导出 ZIP 资源包"
        bl_options = {"REGISTER"}
        filename_ext = ".zip"
        filter_glob: StringProperty(default="*.zip", options={"HIDDEN"})
        overwrite: BoolProperty(name="允许覆盖", default=False)

        def execute(self, context):
            try:
                export_pack_from_scene(context.scene, self.filepath, zip_output=True, overwrite=self.overwrite)
            except Exception as exc:
                self.report({"ERROR"}, f"导出失败: {exc}")
                return {"CANCELLED"}
            self.report({"INFO"}, "ZIP 资源包导出完成")
            return {"FINISHED"}


    class HALO_OT_add_group(bpy.types.Operator):
        bl_idname = "halo.add_group"
        bl_label = "添加部件组"
        bl_options = {"REGISTER", "UNDO"}

        group_id: StringProperty(name="部件 ID", default="new_group")

        def invoke(self, context, event):
            return context.window_manager.invoke_props_dialog(self)

        def execute(self, context):
            parent = _active_object(context)
            if parent is None or parent.get("halo_role") == PRIMITIVE_ROLE:
                parent = _active_root(context)
            if parent is None:
                self.report({"ERROR"}, "请先选择光环根或部件组")
                return {"CANCELLED"}
            definition_id = parent.get("halo_definition_id", "")
            collection = parent.users_collection[0] if parent.users_collection else blender_scene._ensure_collection(context.scene)
            raw = {"id": self.group_id.strip() or "new_group", "position": [0, 0, 0], "rotation": [0, 0, 0], "scale": 1.0, "animation": {}, "children": []}
            path = f"manual/{uuid.uuid4().hex[:8]}"
            obj = blender_scene._make_group(collection, parent, raw, definition_id, path)
            _select_object(context, obj)
            return {"FINISHED"}


    class HALO_OT_add_primitive(bpy.types.Operator):
        bl_idname = "halo.add_primitive"
        bl_label = "添加图元"
        bl_options = {"REGISTER", "UNDO"}

        primitive_type: EnumProperty(name="类型", items=(("billboard", "Billboard", "水平四边形"), ("ring", "Ring", "圆环")), default="billboard")
        texture: StringProperty(name="纹理 ID", default="minecraft:textures/halo/example.png")

        def invoke(self, context, event):
            return context.window_manager.invoke_props_dialog(self)

        def execute(self, context):
            parent = _active_object(context)
            if parent is None or parent.get("halo_role") == PRIMITIVE_ROLE:
                self.report({"ERROR"}, "请先选择部件组")
                return {"CANCELLED"}
            if parent.get("halo_role") == ROOT_ROLE:
                self.report({"ERROR"}, "图元必须放在部件组内；请先添加或选择部件组")
                return {"CANCELLED"}
            collection = parent.users_collection[0] if parent.users_collection else blender_scene._ensure_collection(context.scene)
            primitive = {"type": self.primitive_type, "texture": self.texture, "size": [1.0, 1.0]}
            if self.primitive_type == "ring":
                primitive = {"type": "ring", "texture": self.texture, "size": [0.5, 0.05], "segments": 64}
            parent_node = getattr(parent, "halo_node", None)
            glowing = bool(parent_node.glowing) if parent_node is not None else True
            obj = blender_scene._make_primitive(collection, parent, primitive, parent.get("halo_definition_id", ""), f"manual/primitive/{uuid.uuid4().hex[:8]}", glowing)
            _select_object(context, obj)
            return {"FINISHED"}


    class HALO_OT_select_parent_group(bpy.types.Operator):
        bl_idname = "halo.select_parent_group"
        bl_label = "选择所属部件组"
        bl_options = {"REGISTER"}

        @classmethod
        def poll(cls, context):
            obj = _active_object(context)
            return obj is not None and obj.get("halo_role") == PRIMITIVE_ROLE and obj.parent is not None and obj.parent.get("halo_role") == GROUP_ROLE

        def execute(self, context):
            obj = _active_object(context)
            parent = obj.parent if obj is not None else None
            if parent is None or parent.get("halo_role") != GROUP_ROLE:
                self.report({"ERROR"}, "当前图元没有有效的所属部件组")
                return {"CANCELLED"}
            _select_object(context, parent)
            return {"FINISHED"}


    class HALO_OT_nudge_transform(bpy.types.Operator):
        bl_idname = "halo.nudge_transform"
        bl_label = "步进调整部件变换"
        bl_options = {"REGISTER", "UNDO"}

        target: EnumProperty(
            name="属性",
            items=(("position", "位置", ""), ("rotation", "旋转", ""), ("scale", "缩放", "")),
            default="position",
            options={"HIDDEN"},
        )
        axis: IntProperty(name="轴", default=0, min=0, max=2, options={"HIDDEN"})
        direction: IntProperty(name="方向", default=1, min=-1, max=1, options={"HIDDEN"})

        @classmethod
        def poll(cls, context):
            obj = _active_object(context)
            return obj is not None and obj.get("halo_role") in {GROUP_ROLE, PRIMITIVE_ROLE}

        def execute(self, context):
            obj = _active_object(context)
            if obj is not None and obj.get("halo_role") == PRIMITIVE_ROLE:
                obj = obj.parent
            if obj is None or obj.get("halo_role") != GROUP_ROLE or getattr(obj, "halo_node", None) is None:
                self.report({"ERROR"}, "请选择部件组或其图元")
                return {"CANCELLED"}
            project = context.scene.halo_project
            prefix = "coarse" if project.transform_precision == "COARSE" else "fine"
            step_name = f"{prefix}_{self.target}_step"
            step = float(getattr(project, step_name)) * (-1.0 if self.direction < 0 else 1.0)
            node = obj.halo_node
            if self.target == "scale":
                node.scale = float(node.scale) + step
            else:
                values = list(getattr(node, self.target))
                values[int(self.axis)] += step
                setattr(node, self.target, values)
            return {"FINISHED"}


    class HALO_OT_duplicate_node(bpy.types.Operator):
        bl_idname = "halo.duplicate_node"
        bl_label = "在当前父级复制"
        bl_description = "在当前父级下创建所选组或图元的独立副本"
        bl_options = {"REGISTER", "UNDO"}

        @classmethod
        def poll(cls, context):
            obj = _active_object(context)
            return obj is not None and obj.get("halo_role") in {GROUP_ROLE, PRIMITIVE_ROLE}

        def execute(self, context):
            obj = _active_object(context)
            if obj is None or obj.get("halo_role") not in {GROUP_ROLE, PRIMITIVE_ROLE}:
                self.report({"ERROR"}, "请选择要复制的部件组或图元")
                return {"CANCELLED"}
            try:
                clone = _duplicate_node_from_json(context, obj)
            except Exception as exc:
                self.report({"ERROR"}, str(exc))
                return {"CANCELLED"}
            _select_object(context, clone)
            role_name = "部件组" if clone.get("halo_role") == GROUP_ROLE else "图元"
            self.report({"INFO"}, f"已在当前父级下复制{role_name}")
            return {"FINISHED"}


    class HALO_OT_delete_node(bpy.types.Operator):
        bl_idname = "halo.delete_node"
        bl_label = "删除部件"
        bl_options = {"REGISTER", "UNDO"}

        def execute(self, context):
            obj = _active_object(context)
            if obj is None:
                self.report({"ERROR"}, "请选择 Halo 部件")
                return {"CANCELLED"}
            definition_id = obj.get("halo_definition_id", "")
            remove_object_tree(obj)
            context.scene.halo_project.active_uuid = ""
            context.scene.halo_project.active_definition = definition_id
            return {"FINISHED"}


    class HALO_OT_reparent(bpy.types.Operator):
        bl_idname = "halo.reparent"
        bl_label = "选择新父级"
        bl_description = "把当前组移动到光环根或另一个组下"
        bl_options = {"REGISTER", "UNDO"}

        target_uuid: EnumProperty(name="新父级", description="选择光环根或同一光环定义中的另一个组", items=_reparent_target_items)
        preserve_world: BoolProperty(
            name="保持世界外观",
            description="重新计算局部 JSON 变换，使移动父级前后的世界位置和朝向不变；关闭后保留原局部 JSON 值",
            default=True,
        )
        carry_position: BoolProperty(name="位置", description="携带位置；取消后移动完成时重置为 [0, 0, 0]", default=True, options={"SKIP_SAVE"})
        carry_rotation: BoolProperty(name="旋转", description="携带旋转；取消后移动完成时重置为 [0, 0, 0]", default=True, options={"SKIP_SAVE"})
        carry_scale: BoolProperty(name="缩放", description="携带统一缩放；取消后移动完成时重置为 1", default=True, options={"SKIP_SAVE"})
        carry_animation: BoolProperty(name="常驻动画", description="携带组内 animation；取消后清空常驻动画", default=True, options={"SKIP_SAVE"})
        carry_render: BoolProperty(name="发光与继承", description="携带 glowing、inherit_alpha 和 inherit_glow；取消后使用默认值 true", default=True, options={"SKIP_SAVE"})
        carry_extra: BoolProperty(name="扩展/未知字段", description="携带标准编辑器尚未识别的组字段", default=True, options={"SKIP_SAVE"})

        @classmethod
        def poll(cls, context):
            obj = _active_object(context)
            return obj is not None and obj.get("halo_role") == GROUP_ROLE

        def invoke(self, context, _event):
            items = _reparent_target_items(self, context)
            valid_ids = {item[0] for item in items}
            obj = _active_object(context)
            current_parent_uuid = str(obj.parent.get("halo_uuid", "")) if obj is not None and obj.parent is not None else ""
            if current_parent_uuid in valid_ids:
                self.target_uuid = current_parent_uuid
            elif items:
                self.target_uuid = items[0][0]
            return context.window_manager.invoke_props_dialog(self, width=460)

        def draw(self, context):
            layout = self.layout
            obj = _active_object(context)
            layout.label(text=f"移动组：{_group_display_name(obj)}", icon="CONSTRAINT_BONE")
            layout.prop(self, "target_uuid")
            layout.prop(self, "preserve_world")
            _draw_carry_options(layout, self, include_transition=False)
            if self.preserve_world and not (self.carry_position and self.carry_rotation and self.carry_scale):
                layout.label(text="未携带的变换会在保持世界外观后重置", icon="INFO")

        def execute(self, context):
            obj = _active_object(context)
            target = object_by_uuid(self.target_uuid)
            if obj is None or target is None:
                self.report({"ERROR"}, "请选择组和有效的新父级")
                return {"CANCELLED"}
            node = getattr(obj, "halo_node", None)
            local_snapshot = None
            if node is not None:
                local_snapshot = (tuple(node.position), tuple(node.rotation), float(node.scale))
            try:
                reparent_object(obj, target, self.preserve_world)
                if not self.preserve_world and node is not None and local_snapshot is not None:
                    obj["halo_property_update_guard"] = True
                    try:
                        node.position = local_snapshot[0]
                        node.rotation = local_snapshot[1]
                        node.scale = local_snapshot[2]
                    finally:
                        obj.pop("halo_property_update_guard", None)
                _apply_group_property_selection(
                    obj,
                    carry_position=self.carry_position,
                    carry_rotation=self.carry_rotation,
                    carry_scale=self.carry_scale,
                    carry_animation=self.carry_animation,
                    carry_render=self.carry_render,
                    carry_extra=self.carry_extra,
                )
                sync_definition_from_scene(context.scene, obj.get("halo_definition_id", ""))
            except Exception as exc:
                self.report({"ERROR"}, str(exc))
                return {"CANCELLED"}
            context.scene.halo_project.preserve_world_on_reparent = self.preserve_world
            self.report({"INFO"}, f"已将 {_group_display_name(obj)} 移动到 {_group_display_name(target)}")
            return {"FINISHED"}


    class HALO_OT_move_primitive(bpy.types.Operator):
        bl_idname = "halo.move_primitive"
        bl_label = "迁移图元到其他父级"
        bl_description = "复制所属组的属性生成新组，并把当前图元移动到该新组中"
        bl_options = {"REGISTER", "UNDO"}

        target_uuid: EnumProperty(
            name="目标父级",
            description="新属性副本组将成为这个光环根或部件组的子组",
            items=_primitive_move_target_items,
        )
        new_group_id: StringProperty(
            name="新部件组 ID",
            description="用于承载被迁移图元的新组 ID；必须在当前光环定义中唯一",
            default="",
        )
        carry_position: BoolProperty(name="位置", description="把原所属组的位置复制到新组", default=True, options={"SKIP_SAVE"})
        carry_rotation: BoolProperty(name="旋转", description="把原所属组的旋转复制到新组", default=True, options={"SKIP_SAVE"})
        carry_scale: BoolProperty(name="缩放", description="把原所属组的统一缩放复制到新组", default=True, options={"SKIP_SAVE"})
        carry_animation: BoolProperty(name="常驻动画", description="把原所属组的 animation 复制到新组", default=True, options={"SKIP_SAVE"})
        carry_render: BoolProperty(name="发光与继承", description="复制 glowing、inherit_alpha 和 inherit_glow", default=True, options={"SKIP_SAVE"})
        carry_extra: BoolProperty(name="扩展/未知字段", description="复制标准编辑器尚未识别的组字段", default=True, options={"SKIP_SAVE"})
        carry_transition: BoolProperty(name="启动/关闭过渡", description="把原组 ID 的 startup/shutdown id_overrides 复制到新组 ID", default=True, options={"SKIP_SAVE"})

        @classmethod
        def poll(cls, context):
            obj = _active_object(context)
            return obj is not None and obj.get("halo_role") == PRIMITIVE_ROLE and obj.parent is not None and obj.parent.get("halo_role") == GROUP_ROLE

        def invoke(self, context, _event):
            obj = _active_object(context)
            source_group = obj.parent if obj is not None else None
            source_node = getattr(source_group, "halo_node", None)
            source_id = str(getattr(source_node, "node_id", "") or "group")
            definition_id = obj.get("halo_definition_id", "") if obj is not None else ""
            self.new_group_id = _unique_group_id(definition_id, source_id + "_moved")
            items = _primitive_move_target_items(self, context)
            valid_ids = {item[0] for item in items}
            current_container_uuid = str(source_group.parent.get("halo_uuid", "")) if source_group is not None and source_group.parent is not None else ""
            if current_container_uuid in valid_ids:
                self.target_uuid = current_container_uuid
            elif items:
                self.target_uuid = items[0][0]
            return context.window_manager.invoke_props_dialog(self, width=480)

        def draw(self, context):
            layout = self.layout
            obj = _active_object(context)
            source_group = obj.parent if obj is not None else None
            layout.label(text=f"原所属组：{_group_display_name(source_group)}", icon="OUTLINER_OB_EMPTY")
            layout.prop(self, "target_uuid")
            layout.prop(self, "new_group_id")
            _draw_carry_options(layout, self, include_transition=True)
            layout.label(text="不会复制原组中的其他图元或子组", icon="INFO")

        def execute(self, context):
            primitive = _active_object(context)
            source_group = primitive.parent if primitive is not None else None
            target = object_by_uuid(self.target_uuid)
            if primitive is None or primitive.get("halo_role") != PRIMITIVE_ROLE or source_group is None or source_group.get("halo_role") != GROUP_ROLE:
                self.report({"ERROR"}, "请选择具有有效所属组的图元")
                return {"CANCELLED"}
            if target is None or target.get("halo_role") not in {ROOT_ROLE, GROUP_ROLE}:
                self.report({"ERROR"}, "请选择有效的目标父级")
                return {"CANCELLED"}
            definition_id = primitive.get("halo_definition_id", "")
            if target.get("halo_definition_id") != definition_id:
                self.report({"ERROR"}, "不能把图元迁移到另一个光环定义")
                return {"CANCELLED"}
            if target == source_group:
                self.report({"ERROR"}, "目标父级不能是图元当前所属组")
                return {"CANCELLED"}
            new_group_id = self.new_group_id.strip()
            if not new_group_id:
                self.report({"ERROR"}, "新部件组 ID 不能为空")
                return {"CANCELLED"}
            used_ids = {
                str(getattr(getattr(obj, "halo_node", None), "node_id", "") or "")
                for obj in bpy.data.objects
                if obj.get("halo_role") == GROUP_ROLE and obj.get("halo_definition_id") == definition_id
            }
            if new_group_id in used_ids:
                self.report({"ERROR"}, f"部件组 ID 已存在：{new_group_id}")
                return {"CANCELLED"}

            try:
                source_raw = blender_scene._sync_group(source_group)
                raw = _selected_group_property_copy(
                    source_raw,
                    new_group_id,
                    carry_position=self.carry_position,
                    carry_rotation=self.carry_rotation,
                    carry_scale=self.carry_scale,
                    carry_animation=self.carry_animation,
                    carry_render=self.carry_render,
                    carry_extra=self.carry_extra,
                )
                collection = target.users_collection[0] if target.users_collection else blender_scene._ensure_collection(context.scene)
                new_group = blender_scene._make_group(
                    collection,
                    target,
                    raw,
                    definition_id,
                    f"manual/moved/{uuid.uuid4().hex[:12]}",
                )
                source_group_id = str(getattr(getattr(source_group, "halo_node", None), "node_id", "") or "").strip()
                if source_group_id and self.carry_transition:
                    _copy_transition_overrides(context.scene, definition_id, [(source_group_id, new_group_id)])
                primitive.parent = new_group
                blender_scene.reset_primitive_transform(primitive)
                primitive["halo_parent_uuid"] = new_group.get("halo_uuid", "")
                primitive["halo_primitive_index"] = 0
                if getattr(primitive, "halo_node", None) is not None:
                    primitive.halo_node.parent_uuid = primitive["halo_parent_uuid"]
                blender_scene._sync_group(source_group)
                blender_scene._sync_group(new_group)
                sync_definition_from_scene(context.scene, definition_id)
            except Exception as exc:
                self.report({"ERROR"}, f"迁移失败：{exc}")
                return {"CANCELLED"}
            _select_object(context, primitive)
            self.report({"INFO"}, f"已将图元迁移到新组 {new_group_id}")
            return {"FINISHED"}


    class HALO_OT_refresh_geometry(bpy.types.Operator):
        bl_idname = "halo.refresh_geometry"
        bl_label = "更新图元几何"
        bl_options = {"REGISTER", "UNDO"}

        def execute(self, context):
            obj = _active_object(context)
            if obj is None or obj.get("halo_role") != PRIMITIVE_ROLE:
                self.report({"ERROR"}, "请选择 Billboard 或 Ring 图元")
                return {"CANCELLED"}
            _set_node_mesh(obj)
            return {"FINISHED"}


    class HALO_OT_import_texture(bpy.types.Operator, ImportHelper):
        bl_idname = "halo.import_texture"
        bl_label = "导入/重链接 PNG"
        bl_options = {"REGISTER", "UNDO"}
        filename_ext = ".png"
        filter_glob: StringProperty(default="*.png", options={"HIDDEN"})
        target: EnumProperty(
            name="材质面",
            items=(("OUTER", "外侧", "Billboard 或 Ring 外侧纹理"), ("INNER", "内侧", "Ring 内侧纹理")),
            default="OUTER",
            options={"HIDDEN"},
        )

        def execute(self, context):
            if Path(self.filepath).suffix.lower() != ".png":
                self.report({"ERROR"}, "Halo 资源贴图必须是 PNG")
                return {"CANCELLED"}
            obj = _active_object(context)
            if obj is None or obj.get("halo_role") != PRIMITIVE_ROLE:
                self.report({"ERROR"}, "请先选择一个图元")
                return {"CANCELLED"}
            node = obj.halo_node
            if self.target == "INNER" and node.primitive_type != "ring":
                self.report({"ERROR"}, "只有 Ring 图元支持独立内侧纹理")
                return {"CANCELLED"}
            project = context.scene.halo_project
            pack_root = project.pack_root
            if not pack_root or not os.path.isdir(pack_root):
                pack_root = tempfile.mkdtemp(prefix="halo_pack_edit_")
                project.pack_root = pack_root
            old_id = (node.inner_texture if self.target == "INNER" else node.texture) or node.texture or "minecraft:textures/halo/imported.png"
            namespace, relative = split_resource_id(old_id)
            filename = Path(self.filepath).name
            if not relative or relative.endswith("/"):
                relative = "textures/halo/" + filename
            else:
                relative = relative.rsplit("/", 1)[0] + "/" + filename if "/" in relative else "textures/halo/" + filename
            texture_id = f"{namespace}:{relative}"
            copied = copy_texture_with_sidecars(self.filepath, pack_root, texture_id)
            if not copied:
                self.report({"ERROR"}, "无法复制所选贴图")
                return {"CANCELLED"}
            actual = Path(copied[0]).resolve()
            assets_root = Path(pack_root).resolve() / "assets" / namespace
            actual_relative = actual.relative_to(assets_root).as_posix()
            texture_id = f"{namespace}:{actual_relative}"
            if self.target == "INNER":
                node.inner_texture = texture_id
                obj["halo_inner_texture_id"] = texture_id
            else:
                node.texture = texture_id
                obj["halo_texture_id"] = texture_id
            _set_node_mesh(obj)
            side = "内侧" if self.target == "INNER" else "外侧"
            self.report({"INFO"}, f"已链接{side}纹理 {texture_id}")
            return {"FINISHED"}


    class HALO_OT_clear_inner_texture(bpy.types.Operator):
        bl_idname = "halo.clear_inner_texture"
        bl_label = "内侧使用外侧纹理"
        bl_options = {"REGISTER", "UNDO"}

        @classmethod
        def poll(cls, context):
            obj = _active_object(context)
            return obj is not None and obj.get("halo_role") == PRIMITIVE_ROLE and obj.halo_node.primitive_type == "ring"

        def execute(self, context):
            obj = _active_object(context)
            if obj is None or obj.get("halo_role") != PRIMITIVE_ROLE or obj.halo_node.primitive_type != "ring":
                return {"CANCELLED"}
            obj.halo_node.inner_texture = ""
            obj["halo_inner_texture_id"] = ""
            _set_node_mesh(obj)
            self.report({"INFO"}, "Ring 内侧已改为使用外侧纹理")
            return {"FINISHED"}


    class HALO_OT_validate(bpy.types.Operator):
        bl_idname = "halo.validate"
        bl_label = "验证资源包"

        def execute(self, context):
            result = validate_scene(context.scene)
            if result["errors"]:
                self.report({"WARNING"}, f"验证完成：{len(result['errors'])} 个错误，{len(result['warnings'])} 个警告")
            else:
                self.report({"INFO"}, f"验证通过，{len(result['warnings'])} 个警告")
            return {"FINISHED"}


    class HALO_OT_open_raw_json(bpy.types.Operator):
        bl_idname = "halo.open_raw_json"
        bl_label = "在文本编辑器打开 JSON"

        def execute(self, context):
            obj = _active_object(context)
            definition_id = obj.get("halo_definition_id", "") if obj else context.scene.halo_project.active_definition
            item = next((entry for entry in context.scene.halo_project.definitions if entry.definition_id == definition_id), None)
            if item is None:
                self.report({"ERROR"}, "没有可编辑的光环定义")
                return {"CANCELLED"}
            name = "halo_raw_" + blender_scene._safe_name(definition_id) + ".json"
            text = bpy.data.texts.get(name) or bpy.data.texts.new(name)
            text.clear()
            text.write(item.raw_json)
            context.scene.halo_project.raw_text_name = name
            self.report({"INFO"}, f"已打开文本块 {name}；编辑后执行“应用 JSON”")
            return {"FINISHED"}


    class HALO_OT_apply_raw_json(bpy.types.Operator):
        bl_idname = "halo.apply_raw_json"
        bl_label = "应用并重新解析 JSON"
        bl_options = {"REGISTER", "UNDO"}

        def execute(self, context):
            name = context.scene.halo_project.raw_text_name
            text = bpy.data.texts.get(name) if name else None
            if text is None:
                self.report({"ERROR"}, "请先打开原始 JSON 文本块")
                return {"CANCELLED"}
            try:
                raw = json.loads(text.as_string())
                if not isinstance(raw, Mapping):
                    raise ValueError("JSON 根节点必须是对象")
                definition_id = str(raw.get("id", context.scene.halo_project.active_definition))
                if not definition_id:
                    raise ValueError("缺少 id")
                old_item = next((entry for entry in context.scene.halo_project.definitions if entry.definition_id == definition_id), None)
                source_path = old_item.source_path if old_item else ""
                import_definition_to_scene(context.scene, {"id": definition_id, "raw": raw, "source_path": source_path}, replace=True)
            except Exception as exc:
                self.report({"ERROR"}, f"JSON 无法应用: {exc}")
                return {"CANCELLED"}
            self.report({"INFO"}, "JSON 已重新解析并同步到场景")
            return {"FINISHED"}


    class HALO_OT_sync_scene(bpy.types.Operator):
        bl_idname = "halo.sync_scene"
        bl_label = "从场景同步 JSON"
        bl_options = {"REGISTER", "UNDO"}

        def execute(self, context):
            count = len(sync_all_definitions(context.scene))
            self.report({"INFO"}, f"已同步 {count} 个光环定义")
            return {"FINISHED"}


    class HALO_OT_set_preview_space(bpy.types.Operator):
        bl_idname = "halo.set_preview_space"
        bl_label = "切换预览坐标系"
        bl_options = {"REGISTER", "UNDO"}

        space: EnumProperty(name="坐标系", items=(("HALO_LOCAL", "光环局部", ""), ("MC_HEAD", "MC 头部", "")), default="HALO_LOCAL")

        def execute(self, context):
            context.scene.halo_project.preview_space = self.space
            update_preview_roots(context.scene)
            return {"FINISHED"}


    class HALO_OT_play_preview(bpy.types.Operator):
        bl_idname = "halo.play_preview"
        bl_label = "播放动画预览"

        mode: EnumProperty(name="模式", items=(("IDLE", "常驻", ""), ("STARTUP", "启动", ""), ("SHUTDOWN", "关闭", ""), ("SEQUENCE", "完整序列", "")), default="IDLE")

        def execute(self, context):
            project = context.scene.halo_project
            project.preview_mode = self.mode
            context.scene.render.fps = int(project.preview_fps)
            context.scene.frame_start = 1
            context.scene.frame_end = max(2, int(project.preview_fps * (project.transition_duration if self.mode != "IDLE" else 5.0)))
            bpy.ops.screen.animation_play()
            return {"FINISHED"}


    class HALO_OT_stop_preview(bpy.types.Operator):
        bl_idname = "halo.stop_preview"
        bl_label = "停止动画预览"

        def execute(self, context):
            if context.screen:
                bpy.ops.screen.animation_cancel(restore_frame=False)
            context.scene.halo_project.preview_mode = "IDLE"
            return {"FINISHED"}


    class HALO_OT_animation_term_add(bpy.types.Operator):
        bl_idname = "halo.animation_term_add"
        bl_label = "添加常驻动画项"
        bl_options = {"REGISTER", "UNDO"}

        function: EnumProperty(name="函数", items=(("sin", "sin", ""), ("cos", "cos", ""), ("linear", "linear", "")), default="sin")
        amplitude: FloatProperty(name="A", default=1.0)
        omega: FloatProperty(name="omega", default=1.0)
        phi: FloatProperty(name="phi", default=0.0)
        start: FloatProperty(name="start", default=0.0)
        speed: FloatProperty(name="speed", default=1.0)

        def invoke(self, context, event):
            return context.window_manager.invoke_props_dialog(self)

        def execute(self, context):
            owner = _animation_owner(context)
            if owner is None:
                self.report({"ERROR"}, "请选择光环定义或部件组")
                return {"CANCELLED"}
            try:
                animation = json.loads(owner.animation_json or "{}")
            except (TypeError, ValueError):
                animation = {}
            if not isinstance(animation, dict):
                animation = {}
            terms = _animation_terms(animation, context.scene.halo_project.animation_channel, create=True)
            if self.function in {"sin", "cos"}:
                term = {"function": self.function, "A": self.amplitude, "omega": self.omega, "phi": self.phi}
            else:
                term = {"function": "linear", "start": self.start, "speed": self.speed}
            terms.append(term)
            context.scene.halo_project.animation_term_index = len(terms) - 1
            owner.animation_json = json.dumps(animation, ensure_ascii=False, indent=2)
            return {"FINISHED"}


    class HALO_OT_animation_term_edit(bpy.types.Operator):
        bl_idname = "halo.animation_term_edit"
        bl_label = "编辑常驻动画项"
        bl_options = {"REGISTER", "UNDO"}

        function: EnumProperty(name="函数", items=(("sin", "sin", ""), ("cos", "cos", ""), ("linear", "linear", "")), default="sin")
        amplitude: FloatProperty(name="A", default=0.0)
        omega: FloatProperty(name="omega", default=0.0)
        phi: FloatProperty(name="phi", default=0.0)
        start: FloatProperty(name="start", default=0.0)
        speed: FloatProperty(name="speed", default=0.0)
        index: IntProperty(name="动画项", default=-1, min=-1, options={"HIDDEN"})

        def invoke(self, context, event):
            owner = _animation_owner(context)
            try:
                animation = json.loads(owner.animation_json or "{}")
                terms = _animation_terms(animation, context.scene.halo_project.animation_channel)
                index = self.index if self.index >= 0 else context.scene.halo_project.animation_term_index
                term = terms[index]
            except (AttributeError, TypeError, ValueError, IndexError):
                self.report({"ERROR"}, "所选动画项不存在")
                return {"CANCELLED"}
            self.function = str(term.get("function", "linear"))
            self.amplitude = float(term.get("A", term.get("amplitude", 0.0)))
            self.omega = float(term.get("omega", 0.0))
            self.phi = float(term.get("phi", 0.0))
            self.start = float(term.get("start", 0.0))
            self.speed = float(term.get("speed", 0.0))
            return context.window_manager.invoke_props_dialog(self)

        def execute(self, context):
            owner = _animation_owner(context)
            animation = json.loads(owner.animation_json or "{}")
            terms = _animation_terms(animation, context.scene.halo_project.animation_channel)
            index = self.index if self.index >= 0 else context.scene.halo_project.animation_term_index
            if terms is None or index >= len(terms):
                return {"CANCELLED"}
            terms[index] = ({"function": self.function, "A": self.amplitude, "omega": self.omega, "phi": self.phi}
                            if self.function in {"sin", "cos"} else
                            {"function": "linear", "start": self.start, "speed": self.speed})
            owner.animation_json = json.dumps(animation, ensure_ascii=False, indent=2)
            return {"FINISHED"}


    class HALO_OT_animation_term_remove(bpy.types.Operator):
        bl_idname = "halo.animation_term_remove"
        bl_label = "删除常驻动画项"
        bl_options = {"REGISTER", "UNDO"}

        index: IntProperty(name="动画项", default=-1, min=-1, options={"HIDDEN"})

        def execute(self, context):
            owner = _animation_owner(context)
            try:
                animation = json.loads(owner.animation_json or "{}")
                terms = _animation_terms(animation, context.scene.halo_project.animation_channel)
                index = self.index if self.index >= 0 else context.scene.halo_project.animation_term_index
                terms.pop(index)
            except (AttributeError, TypeError, ValueError, IndexError):
                return {"CANCELLED"}
            owner.animation_json = json.dumps(animation, ensure_ascii=False, indent=2)
            context.scene.halo_project.animation_term_index = max(0, index - 1)
            return {"FINISHED"}


    class HALO_OT_animation_term_move(bpy.types.Operator):
        bl_idname = "halo.animation_term_move"
        bl_label = "移动常驻动画项"
        bl_options = {"REGISTER", "UNDO"}

        direction: IntProperty(name="方向", default=1)
        index: IntProperty(name="动画项", default=-1, min=-1, options={"HIDDEN"})

        def execute(self, context):
            owner = _animation_owner(context)
            try:
                animation = json.loads(owner.animation_json or "{}")
                terms = _animation_terms(animation, context.scene.halo_project.animation_channel)
                old = self.index if self.index >= 0 else context.scene.halo_project.animation_term_index
                new = max(0, min(len(terms) - 1, old + self.direction))
                terms[old], terms[new] = terms[new], terms[old]
            except (AttributeError, TypeError, ValueError, IndexError):
                return {"CANCELLED"}
            owner.animation_json = json.dumps(animation, ensure_ascii=False, indent=2)
            context.scene.halo_project.animation_term_index = new
            return {"FINISHED"}


    class HALO_OT_transition_use_active_group(bpy.types.Operator):
        bl_idname = "halo.transition_use_active_group"
        bl_label = "使用当前组 ID"

        def execute(self, context):
            obj = _active_object(context)
            if obj is not None and obj.get("halo_role") == PRIMITIVE_ROLE:
                obj = obj.parent
            if obj is None or obj.get("halo_role") != GROUP_ROLE:
                self.report({"ERROR"}, "请先在 Outliner 或 3D 视图选择部件组")
                return {"CANCELLED"}
            group_id = str(getattr(obj.halo_node, "node_id", "") or "").strip()
            if not group_id:
                self.report({"ERROR"}, "当前组没有 ID，无法建立 id_overrides")
                return {"CANCELLED"}
            context.scene.halo_project.transition_group_id = group_id
            return {"FINISHED"}


    class HALO_OT_transition_segment_add(bpy.types.Operator):
        bl_idname = "halo.transition_segment_add"
        bl_label = "添加过渡段"
        bl_options = {"REGISTER", "UNDO"}

        duration: FloatProperty(name="时间（秒）", default=0.5, min=0.000001)
        easing: EnumProperty(name="缓动曲线", items=EASING_ITEMS, default="linear")

        def invoke(self, context, event):
            return context.window_manager.invoke_props_dialog(self)

        def execute(self, context):
            try:
                item, target, document, segments, _exists = _transition_segments(context, create=True)
            except ValueError as exc:
                self.report({"ERROR"}, str(exc))
                return {"CANCELLED"}
            segment = {"duration": float(self.duration), "easing": self.easing}
            segments.append(segment)
            context.scene.halo_project.transition_segment_index = len(segments) - 1
            _store_transition_document(item, target, document)
            _refresh_transition_preview(context)
            return {"FINISHED"}


    class HALO_OT_transition_segment_edit(bpy.types.Operator):
        bl_idname = "halo.transition_segment_edit"
        bl_label = "编辑过渡段"
        bl_options = {"REGISTER", "UNDO"}

        index: IntProperty(name="段", default=-1, min=-1, options={"HIDDEN"})
        duration: FloatProperty(name="时间（秒）", default=0.5, min=0.000001)
        easing: EnumProperty(name="缓动曲线", items=EASING_ITEMS, default="linear")

        def invoke(self, context, event):
            try:
                _item, _target, _document, segments, _exists = _transition_segments(context)
                index = self.index if self.index >= 0 else context.scene.halo_project.transition_segment_index
                segment = segments[index]
                self.duration = float(segment.get("duration", 0.0))
                easing = str(segment.get("easing", "linear")).lower().replace("-", "_")
                self.easing = easing if easing in {item[0] for item in EASING_ITEMS} else "linear"
            except (ValueError, IndexError, TypeError, AttributeError) as exc:
                self.report({"ERROR"}, f"所选过渡段不存在：{exc}")
                return {"CANCELLED"}
            return context.window_manager.invoke_props_dialog(self)

        def execute(self, context):
            try:
                item, target, document, segments, _exists = _transition_segments(context)
                index = self.index if self.index >= 0 else context.scene.halo_project.transition_segment_index
                segment = segments[index]
                if not isinstance(segment, dict):
                    raise TypeError("段必须是 JSON 对象")
            except (ValueError, IndexError, TypeError, AttributeError) as exc:
                self.report({"ERROR"}, str(exc))
                return {"CANCELLED"}
            segment["duration"] = float(self.duration)
            segment["easing"] = self.easing
            _store_transition_document(item, target, document)
            _refresh_transition_preview(context)
            return {"FINISHED"}


    class HALO_OT_transition_segment_remove(bpy.types.Operator):
        bl_idname = "halo.transition_segment_remove"
        bl_label = "删除过渡段"
        bl_options = {"REGISTER", "UNDO"}

        index: IntProperty(name="段", default=-1, min=-1, options={"HIDDEN"})

        def execute(self, context):
            try:
                item, target, document, segments, _exists = _transition_segments(context)
                index = self.index if self.index >= 0 else context.scene.halo_project.transition_segment_index
                before_errors = _transition_boundary_errors(segments, target)
                removed = segments.pop(index)
                new_errors = _transition_boundary_errors(segments, target) - before_errors
                if new_errors:
                    segments.insert(index, removed)
                    channel, _boundary_index, endpoint = sorted(new_errors)[0]
                    raise ValueError(f"删除后 {channel} 的新边界缺少必填 {endpoint}")
            except (ValueError, IndexError, TypeError, AttributeError) as exc:
                self.report({"ERROR"}, str(exc))
                return {"CANCELLED"}
            context.scene.halo_project.transition_segment_index = max(0, min(index, len(segments) - 1))
            _store_transition_document(item, target, document)
            _refresh_transition_preview(context)
            return {"FINISHED"}


    class HALO_OT_transition_segment_move(bpy.types.Operator):
        bl_idname = "halo.transition_segment_move"
        bl_label = "移动过渡段"
        bl_options = {"REGISTER", "UNDO"}

        index: IntProperty(name="段", default=-1, min=-1, options={"HIDDEN"})
        direction: IntProperty(name="方向", default=1)

        def execute(self, context):
            try:
                item, target, document, segments, _exists = _transition_segments(context)
                old = self.index if self.index >= 0 else context.scene.halo_project.transition_segment_index
                if old < 0 or old >= len(segments):
                    raise IndexError("所选过渡段不存在")
                new = max(0, min(len(segments) - 1, old + int(self.direction)))
                if new != old:
                    before_errors = _transition_boundary_errors(segments, target)
                    segments[old], segments[new] = segments[new], segments[old]
                    new_errors = _transition_boundary_errors(segments, target) - before_errors
                    if new_errors:
                        segments[old], segments[new] = segments[new], segments[old]
                        channel, _boundary_index, endpoint = sorted(new_errors)[0]
                        raise ValueError(f"移动后 {channel} 的新边界缺少必填 {endpoint}")
            except (ValueError, IndexError, TypeError, AttributeError) as exc:
                self.report({"ERROR"}, str(exc))
                return {"CANCELLED"}
            context.scene.halo_project.transition_segment_index = new
            _store_transition_document(item, target, document)
            _refresh_transition_preview(context)
            return {"FINISHED"}


    class HALO_OT_transition_channel_edit(bpy.types.Operator):
        bl_idname = "halo.transition_channel_edit"
        bl_label = "编辑过渡通道"
        bl_options = {"REGISTER", "UNDO"}

        index: IntProperty(name="段", default=-1, min=-1, options={"HIDDEN"})
        channel: EnumProperty(
            name="通道",
            items=(
                ("offset", "Offset", "位置 [x, y, z]"),
                ("scale", "Scale", "三轴缩放 [x, y, z]"),
                ("alpha", "Alpha", "透明度标量"),
                ("rotation", "Rotation", "YXZ 欧拉角 [yaw, pitch, roll]"),
            ),
            default="offset",
        )
        has_from: BoolProperty(name="填写 from", default=False)
        from_value: FloatVectorProperty(name="From", size=3, default=(0.0, 0.0, 0.0), precision=5)
        scalar_from: FloatProperty(name="From", default=1.0, precision=5)
        has_to: BoolProperty(name="填写 to", default=False)
        to_value: FloatVectorProperty(name="To", size=3, default=(0.0, 0.0, 0.0), precision=5)
        scalar_to: FloatProperty(name="To", default=1.0, precision=5)
        use_duration: BoolProperty(name="独立时间", default=False)
        duration: FloatProperty(name="通道时间（秒）", default=0.5, min=0.000001)
        use_easing: BoolProperty(name="独立缓动", default=False)
        easing: EnumProperty(name="通道缓动曲线", items=EASING_ITEMS, default="linear")
        use_degrees: BoolProperty(name="指定最小旋转行程", default=False)
        degrees: FloatVectorProperty(name="Degrees YXZ", size=3, default=(0.0, 0.0, 0.0), precision=4)

        def draw(self, context):
            layout = self.layout
            target = context.scene.halo_project.transition_target
            required = "from" if target == "startup" else "to"
            layout.label(text=f"{self.channel}；{target} 边界需要 {required}", icon="ANIM")
            row = layout.row(align=True)
            row.prop(self, "has_from")
            values = row.row(align=True)
            values.enabled = self.has_from
            values.prop(self, "scalar_from" if self.channel == "alpha" else "from_value", text="")
            row = layout.row(align=True)
            row.prop(self, "has_to")
            values = row.row(align=True)
            values.enabled = self.has_to
            values.prop(self, "scalar_to" if self.channel == "alpha" else "to_value", text="")
            row = layout.row(align=True)
            row.prop(self, "use_duration")
            values = row.row(align=True)
            values.enabled = self.use_duration
            values.prop(self, "duration", text="")
            row = layout.row(align=True)
            row.prop(self, "use_easing")
            values = row.row(align=True)
            values.enabled = self.use_easing
            values.prop(self, "easing", text="")
            if self.channel == "rotation":
                row = layout.row(align=True)
                row.prop(self, "use_degrees")
                values = row.row(align=True)
                values.enabled = self.use_degrees
                values.prop(self, "degrees", text="")

        def invoke(self, context, event):
            try:
                _item, target, _document, segments, _exists = _transition_segments(context)
                index = self.index if self.index >= 0 else context.scene.halo_project.transition_segment_index
                segment = segments[index]
                if not isinstance(segment, Mapping):
                    raise TypeError("段必须是 JSON 对象")
            except (ValueError, IndexError, TypeError, AttributeError) as exc:
                self.report({"ERROR"}, str(exc))
                return {"CANCELLED"}
            _key, prop = _transition_channel_entry(segment, self.channel)
            identity = _transition_identity(self.channel)
            self.from_value = tuple(identity[:3]) if len(identity) == 3 else (0.0, 0.0, 0.0)
            self.to_value = tuple(identity[:3]) if len(identity) == 3 else (0.0, 0.0, 0.0)
            self.scalar_from = identity[0]
            self.scalar_to = identity[0]
            if prop is None:
                self.has_from = target == "startup"
                self.has_to = target == "shutdown"
                self.use_duration = self.use_easing = self.use_degrees = False
            else:
                from_raw = prop.get("from")
                to_raw = prop.get("to")
                self.has_from = from_raw is not None
                self.has_to = to_raw is not None
                if self.channel == "alpha":
                    if self.has_from:
                        self.scalar_from = float(from_raw[0] if isinstance(from_raw, list) else from_raw)
                    if self.has_to:
                        self.scalar_to = float(to_raw[0] if isinstance(to_raw, list) else to_raw)
                else:
                    if self.has_from:
                        self.from_value = _transition_vector3(from_raw, identity)
                    if self.has_to:
                        self.to_value = _transition_vector3(to_raw, identity)
                self.use_duration = prop.get("duration") is not None
                if self.use_duration:
                    self.duration = max(0.000001, float(prop["duration"]))
                self.use_easing = prop.get("easing") is not None
                if self.use_easing:
                    easing = str(prop["easing"]).lower().replace("-", "_")
                    self.easing = easing if easing in {item[0] for item in EASING_ITEMS} else "linear"
                degrees = prop.get("degrees")
                self.use_degrees = self.channel == "rotation" and degrees is not None
                if self.use_degrees:
                    self.degrees = _transition_vector3(degrees)
            return context.window_manager.invoke_props_dialog(self, width=520)

        def execute(self, context):
            try:
                item, target, document, segments, _exists = _transition_segments(context)
                index = self.index if self.index >= 0 else context.scene.halo_project.transition_segment_index
                segment = segments[index]
                if not isinstance(segment, dict):
                    raise TypeError("段必须是 JSON 对象")
            except (ValueError, IndexError, TypeError, AttributeError) as exc:
                self.report({"ERROR"}, str(exc))
                return {"CANCELLED"}
            if not self.has_from and not self.has_to:
                self.report({"ERROR"}, "通道至少需要 from 或 to")
                return {"CANCELLED"}

            # The Java parser requires from on the first active startup segment
            # and to on the last active shutdown segment for each property.
            active = []
            for candidate_index, candidate in enumerate(segments):
                if candidate_index == index:
                    active.append(candidate_index)
                    continue
                if isinstance(candidate, Mapping):
                    _candidate_key, candidate_prop = _transition_channel_entry(candidate, self.channel)
                    if candidate_prop is not None:
                        active.append(candidate_index)
            if target == "startup" and index == min(active) and not self.has_from:
                self.report({"ERROR"}, "startup 中此通道首次出现的段必须填写 from")
                return {"CANCELLED"}
            if target == "shutdown" and index == max(active) and not self.has_to:
                self.report({"ERROR"}, "shutdown 中此通道最后出现的段必须填写 to")
                return {"CANCELLED"}

            key, existing = _transition_channel_entry(segment, self.channel)
            if existing is None:
                key = self.channel
            prop = dict(existing or {})
            if self.has_from:
                prop["from"] = float(self.scalar_from) if self.channel == "alpha" else [float(value) for value in self.from_value]
            else:
                prop.pop("from", None)
            if self.has_to:
                prop["to"] = float(self.scalar_to) if self.channel == "alpha" else [float(value) for value in self.to_value]
            else:
                prop.pop("to", None)
            if self.use_duration:
                prop["duration"] = float(self.duration)
            else:
                prop.pop("duration", None)
            if self.use_easing:
                prop["easing"] = self.easing
            else:
                prop.pop("easing", None)
            if self.channel == "rotation" and self.use_degrees:
                prop["degrees"] = [float(value) for value in self.degrees]
            else:
                prop.pop("degrees", None)
            segment[key] = prop
            _store_transition_document(item, target, document)
            _refresh_transition_preview(context)
            return {"FINISHED"}


    class HALO_OT_transition_channel_remove(bpy.types.Operator):
        bl_idname = "halo.transition_channel_remove"
        bl_label = "删除过渡通道"
        bl_options = {"REGISTER", "UNDO"}

        index: IntProperty(name="段", default=-1, min=-1, options={"HIDDEN"})
        channel: EnumProperty(
            name="通道",
            items=(("offset", "Offset", ""), ("scale", "Scale", ""), ("alpha", "Alpha", ""), ("rotation", "Rotation", "")),
            default="offset",
        )

        def execute(self, context):
            try:
                item, target, document, segments, _exists = _transition_segments(context)
                index = self.index if self.index >= 0 else context.scene.halo_project.transition_segment_index
                segment = segments[index]
                key, existing = _transition_channel_entry(segment, self.channel)
                if existing is None:
                    raise KeyError("此段没有该通道")
                before_errors = _transition_boundary_errors(segments, target)
                segment.pop(key, None)
                new_errors = _transition_boundary_errors(segments, target) - before_errors
                if new_errors:
                    segment[key] = dict(existing)
                    channel, _boundary_index, endpoint = sorted(new_errors)[0]
                    raise ValueError(f"删除后 {channel} 的新边界缺少必填 {endpoint}")
            except (ValueError, IndexError, TypeError, AttributeError, KeyError) as exc:
                self.report({"ERROR"}, str(exc))
                return {"CANCELLED"}
            _store_transition_document(item, target, document)
            _refresh_transition_preview(context)
            return {"FINISHED"}


    class HALO_OT_transition_override_clear(bpy.types.Operator):
        bl_idname = "halo.transition_override_clear"
        bl_label = "删除当前 ID 覆盖"
        bl_options = {"REGISTER", "UNDO"}

        def execute(self, context):
            project = context.scene.halo_project
            group_id = str(project.transition_group_id or TRANSITION_DEFAULT_GROUP)
            if group_id == TRANSITION_DEFAULT_GROUP:
                self.report({"ERROR"}, "默认 segments 不是 ID 覆盖")
                return {"CANCELLED"}
            try:
                item, target, document = _transition_document(context)
                overrides = document.get("id_overrides")
                if not isinstance(overrides, dict) or group_id not in overrides:
                    raise KeyError("当前组没有独立 ID 覆盖")
                overrides.pop(group_id)
                if not overrides:
                    document.pop("id_overrides", None)
            except (ValueError, KeyError) as exc:
                self.report({"ERROR"}, str(exc))
                return {"CANCELLED"}
            project.transition_segment_index = 0
            _store_transition_document(item, target, document)
            _refresh_transition_preview(context)
            return {"FINISHED"}


    class HALO_OT_open_animation_json(bpy.types.Operator):
        bl_idname = "halo.open_animation_json"
        bl_label = "多行编辑动画 JSON"

        target: EnumProperty(
            name="动画类型",
            items=(("resident", "常驻动画", ""), ("startup", "启动过渡", ""), ("shutdown", "关闭过渡", "")),
            default="resident",
        )

        def execute(self, context):
            project = context.scene.halo_project
            definition_id = project.active_definition
            item = next((entry for entry in project.definitions if entry.definition_id == definition_id), None)
            node_uuid = ""
            if self.target == "resident":
                owner = _animation_owner(context)
                if owner is None:
                    self.report({"ERROR"}, "请选择光环定义或部件组")
                    return {"CANCELLED"}
                payload = owner.animation_json or "{}"
                obj = _active_object(context)
                if obj is not None and obj.get("halo_role") == PRIMITIVE_ROLE:
                    obj = obj.parent
                if obj is not None and obj.get("halo_role") == GROUP_ROLE:
                    node_uuid = obj.get("halo_uuid", "")
                    definition_id = obj.get("halo_definition_id", definition_id)
            else:
                if item is None:
                    self.report({"ERROR"}, "请先选择光环定义")
                    return {"CANCELLED"}
                payload = item.startup_json if self.target == "startup" else item.shutdown_json
            label = {"resident": "resident", "startup": "startup", "shutdown": "shutdown"}[self.target]
            suffix = node_uuid[:8] if node_uuid else blender_scene._safe_name(definition_id)
            name = f"halo_{label}_{suffix}.json"
            text = bpy.data.texts.get(name) or bpy.data.texts.new(name)
            text.clear()
            text.write(payload or "{}")
            text["halo_animation_editor"] = True
            text["halo_animation_target"] = self.target
            text["halo_definition_id"] = definition_id
            text["halo_node_uuid"] = node_uuid
            project.raw_text_name = name
            if context.area is not None:
                context.area.type = "TEXT_EDITOR"
                context.area.spaces.active.text = text
            self.report({"INFO"}, f"已打开多行动画 JSON：{name}")
            return {"FINISHED"}


    class HALO_OT_apply_animation_json(bpy.types.Operator):
        bl_idname = "halo.apply_animation_json"
        bl_label = "应用动画 JSON"
        bl_options = {"REGISTER", "UNDO"}

        def execute(self, context):
            text = getattr(getattr(context, "space_data", None), "text", None)
            if text is None or not text.get("halo_animation_editor"):
                name = context.scene.halo_project.raw_text_name
                text = bpy.data.texts.get(name) if name else None
            if text is None or not text.get("halo_animation_editor"):
                self.report({"ERROR"}, "当前没有 Halo 动画 JSON 文本")
                return {"CANCELLED"}
            try:
                parsed = json.loads(text.as_string())
                if not isinstance(parsed, Mapping):
                    raise ValueError("动画 JSON 根节点必须是对象")
            except Exception as exc:
                self.report({"ERROR"}, f"动画 JSON 无法应用：{exc}")
                return {"CANCELLED"}
            payload = json.dumps(parsed, ensure_ascii=False, indent=2)
            target = str(text.get("halo_animation_target", "resident"))
            definition_id = str(text.get("halo_definition_id", ""))
            node_uuid = str(text.get("halo_node_uuid", ""))
            project = context.scene.halo_project
            if target == "resident" and node_uuid:
                obj = object_by_uuid(node_uuid)
                if obj is None or obj.get("halo_role") != GROUP_ROLE:
                    self.report({"ERROR"}, "原部件组已不存在")
                    return {"CANCELLED"}
                obj.halo_node.animation_json = payload
                obj["halo_animation_json"] = json.dumps(parsed, ensure_ascii=False, separators=(",", ":"))
                raw = blender_scene._raw_from_object(obj)
                raw["animation"] = parsed
                obj["halo_raw_json"] = json.dumps(raw, ensure_ascii=False, separators=(",", ":"))
            else:
                item = next((entry for entry in project.definitions if entry.definition_id == definition_id), None)
                if item is None:
                    self.report({"ERROR"}, "原光环定义已不存在")
                    return {"CANCELLED"}
                if target == "startup":
                    item.startup_json = payload
                elif target == "shutdown":
                    item.shutdown_json = payload
                else:
                    item.animation_json = payload
            self.report({"INFO"}, "动画 JSON 已应用")
            return {"FINISHED"}


    class HALO_OT_return_3d_view(bpy.types.Operator):
        bl_idname = "halo.return_3d_view"
        bl_label = "返回 3D 视图"

        def execute(self, context):
            if context.area is not None:
                context.area.type = "VIEW_3D"
            return {"FINISHED"}


    OPERATOR_CLASSES = (
        HALO_OT_new_project,
        HALO_OT_new_definition,
        HALO_OT_import_pack,
        HALO_OT_import_folder,
        HALO_OT_export_pack,
        HALO_OT_export_zip,
        HALO_OT_add_group,
        HALO_OT_add_primitive,
        HALO_OT_select_parent_group,
        HALO_OT_nudge_transform,
        HALO_OT_duplicate_node,
        HALO_OT_delete_node,
        HALO_OT_reparent,
        HALO_OT_move_primitive,
        HALO_OT_refresh_geometry,
        HALO_OT_import_texture,
        HALO_OT_clear_inner_texture,
        HALO_OT_validate,
        HALO_OT_open_raw_json,
        HALO_OT_apply_raw_json,
        HALO_OT_sync_scene,
        HALO_OT_set_preview_space,
        HALO_OT_play_preview,
        HALO_OT_stop_preview,
        HALO_OT_animation_term_add,
        HALO_OT_animation_term_edit,
        HALO_OT_animation_term_remove,
        HALO_OT_animation_term_move,
        HALO_OT_transition_use_active_group,
        HALO_OT_transition_segment_add,
        HALO_OT_transition_segment_edit,
        HALO_OT_transition_segment_remove,
        HALO_OT_transition_segment_move,
        HALO_OT_transition_channel_edit,
        HALO_OT_transition_channel_remove,
        HALO_OT_transition_override_clear,
        HALO_OT_open_animation_json,
        HALO_OT_apply_animation_json,
        HALO_OT_return_3d_view,
    )

else:  # pragma: no cover
    OPERATOR_CLASSES = ()


def register_operators():
    if bpy is None:
        return
    for cls in OPERATOR_CLASSES:
        bpy.utils.register_class(cls)


def unregister_operators():
    if bpy is None:
        return
    for cls in reversed(OPERATOR_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass


__all__ = ["OPERATOR_CLASSES", "register_operators", "unregister_operators", "validate_scene"]
