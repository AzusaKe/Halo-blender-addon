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
    from bpy.props import BoolProperty, EnumProperty, FloatProperty, IntProperty, StringProperty
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
    pack_root = getattr(bpy.context.scene.halo_project, "pack_root", "")
    assign_primitive_materials(obj, node.texture, node.inner_texture or None, pack_root, glowing=node.glowing)
    obj["halo_raw_json"] = obj.get("halo_primitive_raw_json", obj.get("halo_raw_json", "{}"))
    return True


def _clone_tree(obj, parent, collection):
    clone = obj.copy()
    if obj.data is not None:
        clone.data = obj.data.copy()
    clone.name = obj.name + " Copy"
    collection.objects.link(clone)
    clone.parent = parent
    clone["halo_uuid"] = uuid.uuid4().hex
    clone["halo_parent_uuid"] = parent.get("halo_uuid", "") if parent else ""
    if clone.get("halo_role") == GROUP_ROLE:
        node = getattr(clone, "halo_node", None)
        if node is not None:
            base = node.node_id or "group"
            node.node_id = base + "_copy"
        try:
            raw = json.loads(clone.get("halo_raw_json", "{}"))
            if isinstance(raw, dict) and "id" in raw:
                raw["id"] = str(raw["id"]) + "_copy"
                clone["halo_raw_json"] = json.dumps(raw, ensure_ascii=False, separators=(",", ":"))
        except (TypeError, ValueError):
            pass
    if clone.get("halo_role") == PRIMITIVE_ROLE:
        try:
            raw = json.loads(clone.get("halo_primitive_raw_json", "{}"))
            clone["halo_primitive_raw_json"] = json.dumps(raw, ensure_ascii=False, separators=(",", ":"))
        except (TypeError, ValueError):
            pass
    for child in obj.children:
        if child.get("halo_role") in {GROUP_ROLE, PRIMITIVE_ROLE}:
            _clone_tree(child, clone, collection)
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
            obj = blender_scene._make_primitive(collection, parent, primitive, parent.get("halo_definition_id", ""), f"manual/primitive/{uuid.uuid4().hex[:8]}", bool(parent.get("halo_raw_json", "{}")))
            _select_object(context, obj)
            return {"FINISHED"}


    class HALO_OT_duplicate_node(bpy.types.Operator):
        bl_idname = "halo.duplicate_node"
        bl_label = "复制部件"
        bl_options = {"REGISTER", "UNDO"}

        def execute(self, context):
            obj = _active_object(context)
            if obj is None or obj.get("halo_role") not in {GROUP_ROLE, PRIMITIVE_ROLE}:
                self.report({"ERROR"}, "请选择要复制的部件组或图元")
                return {"CANCELLED"}
            parent = obj.parent
            collection = obj.users_collection[0] if obj.users_collection else blender_scene._ensure_collection(context.scene)
            clone = _clone_tree(obj, parent, collection)
            _select_object(context, clone)
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
        bl_label = "移动到父级"
        bl_options = {"REGISTER", "UNDO"}

        target_uuid: StringProperty(name="目标父级 UUID", default="")
        preserve_world: BoolProperty(name="保持世界位置", default=True)

        def execute(self, context):
            obj = _active_object(context)
            target = object_by_uuid(self.target_uuid.strip())
            if obj is None or target is None:
                self.report({"ERROR"}, "请选择组，并填写有效的目标父级 UUID")
                return {"CANCELLED"}
            try:
                reparent_object(obj, target, self.preserve_world)
            except Exception as exc:
                self.report({"ERROR"}, str(exc))
                return {"CANCELLED"}
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

        def execute(self, context):
            if Path(self.filepath).suffix.lower() != ".png":
                self.report({"ERROR"}, "Halo 资源贴图必须是 PNG")
                return {"CANCELLED"}
            obj = _active_object(context)
            if obj is None or obj.get("halo_role") != PRIMITIVE_ROLE:
                self.report({"ERROR"}, "请先选择一个图元")
                return {"CANCELLED"}
            project = context.scene.halo_project
            pack_root = project.pack_root
            if not pack_root or not os.path.isdir(pack_root):
                pack_root = tempfile.mkdtemp(prefix="halo_pack_edit_")
                project.pack_root = pack_root
            node = obj.halo_node
            old_id = node.texture or "minecraft:textures/halo/imported.png"
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
            node.texture = texture_id
            obj["halo_texture_id"] = texture_id
            _set_node_mesh(obj)
            self.report({"INFO"}, f"已链接纹理 {texture_id}")
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
        HALO_OT_duplicate_node,
        HALO_OT_delete_node,
        HALO_OT_reparent,
        HALO_OT_refresh_geometry,
        HALO_OT_import_texture,
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
