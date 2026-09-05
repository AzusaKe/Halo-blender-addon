"""Persistent multi-line animation JSON bindings for Blender Text blocks."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping


TARGET_FIELDS = {"resident": "animation", "startup": "startup", "shutdown": "shutdown"}


def text_digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def text_is_pending(text) -> bool:
    return bool(text.get("halo_animation_editor")) and (
        text_digest(text.as_string()) != text.get("halo_animation_applied_sha256", "")
    )


def _document(value):
    try:
        result = json.loads(value or "{}")
    except (TypeError, ValueError):
        result = {}
    return dict(result) if isinstance(result, Mapping) else {}


def _definition_root(scene, item):
    return next((obj for obj in scene.objects if obj.get("halo_role") == "definition_root" and (
        (item.root_uuid and obj.get("halo_uuid") == item.root_uuid)
        or obj.get("halo_definition_id") == item.definition_id
    )), None)


def apply_animation_text(scene, text, *, refresh=True):
    """Apply one bound Text atomically to typed fields and lossless raw ASTs."""
    if text is None or not text.get("halo_animation_editor"):
        raise ValueError("当前没有 Halo 动画 JSON 文本")
    target = str(text.get("halo_animation_target", ""))
    if target not in TARGET_FIELDS:
        raise ValueError(f"未知动画类型：{target}")
    try:
        parsed = json.loads(text.as_string())
    except (TypeError, ValueError) as exc:
        raise ValueError(f"动画 JSON 无法解析：{exc}") from exc
    if not isinstance(parsed, Mapping):
        raise ValueError("动画 JSON 根节点必须是对象")
    parsed = dict(parsed)
    payload = json.dumps(parsed, ensure_ascii=False, indent=2)
    definition_id = str(text.get("halo_definition_id", ""))
    node_uuid = str(text.get("halo_node_uuid", ""))
    project = scene.halo_project

    if target == "resident" and node_uuid:
        obj = next((candidate for candidate in scene.objects if candidate.get("halo_uuid") == node_uuid), None)
        if obj is None or obj.get("halo_role") != "group":
            raise ValueError("原部件组已不存在")
        raw = _document(obj.get("halo_raw_json", "{}"))
        raw["animation"] = parsed
        obj.halo_node.animation_json = payload
        obj.halo_node.raw_json = json.dumps(raw, ensure_ascii=False, indent=2)
        obj["halo_animation_json"] = json.dumps(parsed, ensure_ascii=False, separators=(",", ":"))
        obj["halo_raw_json"] = json.dumps(raw, ensure_ascii=False, separators=(",", ":"))
    else:
        item = next((entry for entry in project.definitions if entry.definition_id == definition_id), None)
        if item is None:
            raise ValueError("原光环定义已不存在")
        field = TARGET_FIELDS[target]
        setattr(item, field + "_json", payload)
        raw = _document(item.raw_json)
        raw[field] = parsed
        item.raw_json = json.dumps(raw, ensure_ascii=False, indent=2)
        root = _definition_root(scene, item)
        if root is not None:
            root_raw = _document(root.get("halo_raw_json", "{}"))
            root_raw[field] = parsed
            root["halo_raw_json"] = json.dumps(root_raw, ensure_ascii=False, indent=2)

    text["halo_animation_applied_sha256"] = text_digest(text.as_string())
    text["halo_animation_last_error"] = ""
    project.pop("halo_animation_text_error", None)
    if refresh:
        from .handlers import update_animation
        update_animation(scene)
    return target, definition_id, node_uuid


def apply_pending_animation_texts(scene, *, strict=False, refresh=False):
    """Apply edited Text blocks belonging to a scene before save or export."""
    import bpy

    applied, errors = [], []
    for text in bpy.data.texts:
        if not text.get("halo_animation_editor") or not text_is_pending(text):
            continue
        bound_scene = str(text.get("halo_scene_name", ""))
        if bound_scene and bound_scene != scene.name:
            continue
        definition_id = str(text.get("halo_definition_id", ""))
        if not any(item.definition_id == definition_id for item in scene.halo_project.definitions):
            continue
        try:
            apply_animation_text(scene, text, refresh=False)
            applied.append(text.name)
        except ValueError as exc:
            message = f"{text.name}：{exc}"
            text["halo_animation_last_error"] = str(exc)
            errors.append(message)
    if errors:
        scene.halo_project["halo_animation_text_error"] = "；".join(errors)
        if strict:
            raise ValueError("动画 JSON 尚未应用：" + "；".join(errors))
    else:
        scene.halo_project.pop("halo_animation_text_error", None)
    if applied and refresh:
        from .handlers import update_animation
        update_animation(scene)
    return {"applied": applied, "errors": errors}


__all__ = ["apply_animation_text", "apply_pending_animation_texts", "text_digest", "text_is_pending"]
