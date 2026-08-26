"""Chinese N-panel and Outliner-friendly controls for the Halo editor."""

from __future__ import annotations

import json

try:
    import bpy
    from bpy.types import Panel, UIList
except ImportError:  # pragma: no cover
    bpy = None


if bpy is not None:

    class HALO_UL_definitions(UIList):
        bl_idname = "HALO_UL_definitions"

        def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
            row = layout.row(align=True)
            icon_name = "OUTLINER_OB_EMPTY" if item.definition_id else "QUESTION"
            row.label(text=item.definition_id or "未命名光环", icon=icon_name)
            if item.schema_version:
                row.label(text=item.schema_version)


    class HALO_PT_project(Panel):
        bl_idname = "HALO_PT_project"
        bl_label = "项目 / 资源包"
        bl_category = "Halo 光环"
        bl_space_type = "VIEW_3D"
        bl_region_type = "UI"

        def draw(self, context):
            layout = self.layout
            project = context.scene.halo_project
            row = layout.row(align=True)
            row.operator("halo.new_project", text="新建", icon="FILE_NEW")
            row.operator("halo.import_pack", text="导入 ZIP", icon="IMPORT")
            row.operator("halo.import_folder", text="导入文件夹", icon="FILE_FOLDER")
            box = layout.box()
            box.label(text="资源包")
            box.prop(project, "pack_root", text="当前目录")
            box.prop(project, "source_path", text="导入源")
            box.prop(project, "manifest_json", text="pack.mcmeta")
            layout.template_list("HALO_UL_definitions", "definitions", project, "definitions", project, "active_definition_index", rows=3)
            row = layout.row(align=True)
            row.operator("halo.new_definition", text="新建光环", icon="ADD")
            row.operator("halo.open_raw_json", text="打开 JSON", icon="TEXT")
            row.operator("halo.apply_raw_json", text="应用 JSON", icon="FILE_REFRESH")


    class HALO_PT_definition(Panel):
        bl_idname = "HALO_PT_definition"
        bl_label = "光环属性"
        bl_parent_id = "HALO_PT_project"
        bl_category = "Halo 光环"
        bl_space_type = "VIEW_3D"
        bl_region_type = "UI"

        @staticmethod
        def _item(context):
            active = context.scene.halo_project.active_definition
            return next((item for item in context.scene.halo_project.definitions if item.definition_id == active), None)

        def draw(self, context):
            layout = self.layout
            item = self._item(context)
            if item is None:
                layout.label(text="请选择一个光环定义")
                return
            layout.prop(item, "definition_id", text="ID")
            layout.prop(item, "schema_version", text="Schema")
            layout.prop(item, "orientation_mode", text="朝向")
            layout.prop(item, "sync_offset", text="同步偏移")
            box = layout.box()
            box.label(text="定位（Minecraft 坐标）")
            box.prop(item, "positioning_offset", text="头部偏移")
            box.prop(item, "positioning_scale", text="整体缩放")
            box = layout.box()
            box.label(text="状态")
            box.prop(item, "hide_on_sleep")
            box.prop(item, "display_in_invisible")
            box.prop(item, "allow_angular_momentum")
            row = layout.row(align=True)
            row.operator("halo.add_group", text="添加部件组", icon="ADD")
            row.operator("halo.sync_scene", text="同步 JSON", icon="FILE_REFRESH")


    class HALO_PT_node(Panel):
        bl_idname = "HALO_PT_node"
        bl_label = "部件 / 图元"
        bl_category = "Halo 光环"
        bl_space_type = "VIEW_3D"
        bl_region_type = "UI"

        @classmethod
        def poll(cls, context):
            obj = context.active_object
            return obj is not None and obj.get("halo_role") in {"group", "primitive", "definition_root"}

        def draw(self, context):
            layout = self.layout
            obj = context.active_object
            role = obj.get("halo_role")
            if role == "definition_root":
                layout.label(text=obj.get("halo_definition_id", "光环根"), icon="EMPTY_AXIS")
                layout.label(text="从左侧项目面板编辑定位和元数据")
                return
            node = getattr(obj, "halo_node", None)
            if node is None:
                layout.label(text="缺少 Halo 节点属性", icon="ERROR")
                return
            layout.prop(node, "node_id", text="部件 ID")
            layout.label(text=f"UUID: {node.uuid}")
            layout.prop(node, "position", text="位置 (MC)")
            layout.prop(node, "rotation", text="旋转 YXZ")
            layout.prop(node, "scale", text="缩放")
            if role == "group":
                box = layout.box()
                box.prop(node, "glowing")
                box.prop(node, "inherit_alpha")
                box.prop(node, "inherit_glow")
                box.label(text="常驻动画请在“动画预览”面板编辑")
                open_json = box.operator("halo.open_animation_json", text="多行编辑本组动画 JSON", icon="TEXT")
                open_json.target = "resident"
                row = box.row(align=True)
                row.operator("halo.add_group", text="添加子组", icon="ADD")
                row.operator("halo.add_primitive", text="添加图元", icon="MESH_PLANE")
            else:
                box = layout.box()
                box.prop(node, "primitive_type", text="类型")
                box.prop(node, "texture", text="纹理")
                if node.primitive_type == "ring":
                    box.prop(node, "inner_texture", text="内侧纹理")
                    box.prop(node, "segments", text="分段")
                box.prop(node, "size", text="尺寸")
                box.prop(node, "face_camera", text="面向相机")
                row = box.row(align=True)
                row.operator("halo.import_texture", text="导入 PNG", icon="IMAGE_DATA")
                row.operator("halo.refresh_geometry", text="更新几何", icon="MESH_DATA")
            row = layout.row(align=True)
            row.operator("halo.duplicate_node", text="复制", icon="DUPLICATE")
            row.operator("halo.delete_node", text="删除", icon="TRASH")


    class HALO_PT_animation(Panel):
        bl_idname = "HALO_PT_animation"
        bl_label = "动画预览"
        bl_category = "Halo 光环"
        bl_space_type = "VIEW_3D"
        bl_region_type = "UI"

        def draw(self, context):
            layout = self.layout
            project = context.scene.halo_project
            box = layout.box()
            box.label(text="预览坐标系")
            row = box.row(align=True)
            row.prop_enum(project, "preview_space", "HALO_LOCAL", text="光环局部")
            row.prop_enum(project, "preview_space", "MC_HEAD", text="MC 玩家头部")
            box.prop(project, "show_head", text="显示 8×8×8 头部")
            box.prop(project, "head_yaw", text="Yaw")
            box.prop(project, "head_pitch", text="Pitch")
            box.prop(project, "head_roll", text="Roll")
            box = layout.box()
            box.label(text="动画")
            box.prop(project, "preview_mode", text="模式")
            box.prop(project, "preview_phase", text="触发相位")
            box.prop(project, "transition_duration", text="过渡时长")
            box.prop(project, "preview_fps", text="FPS")
            row = box.row(align=True)
            play = row.operator("halo.play_preview", text="播放", icon="PLAY")
            play.mode = project.preview_mode
            row.operator("halo.stop_preview", text="停止", icon="PAUSE")
            term_box = layout.box()
            from .operators import _animation_owner, _animation_terms
            owner = _animation_owner(context)
            active_obj = context.active_object
            if active_obj is not None and active_obj.get("halo_role") == "primitive":
                active_obj = active_obj.parent
            target_name = (
                f"部件组：{active_obj.halo_node.node_id or active_obj.name}"
                if active_obj is not None and active_obj.get("halo_role") == "group"
                else f"光环：{project.active_definition or '未选择'}"
            )
            term_box.label(text="常驻动画通道")
            term_box.label(text=target_name, icon="OUTLINER_OB_EMPTY")
            term_box.prop(project, "animation_channel", text="通道")
            terms = []
            parse_error = ""
            if owner is not None:
                try:
                    animation = json.loads(owner.animation_json or "{}")
                    values = _animation_terms(animation, project.animation_channel)
                    terms = values if isinstance(values, list) else []
                except (TypeError, ValueError) as exc:
                    parse_error = str(exc)
            if parse_error:
                term_box.label(text="动画 JSON 无法解析", icon="ERROR")
            elif not terms:
                term_box.label(text="此通道尚未定义动画项", icon="INFO")
            for index, term in enumerate(terms):
                if not isinstance(term, dict):
                    summary = f"#{index + 1} 无效动画项"
                elif str(term.get("function", "linear")) in {"sin", "cos"}:
                    summary = (f"#{index + 1} {term.get('function')}  A={term.get('A', term.get('amplitude', 0))}  "
                               f"ω={term.get('omega', 0)}  φ={term.get('phi', 0)}")
                else:
                    summary = f"#{index + 1} linear  start={term.get('start', 0)}  speed={term.get('speed', 0)}"
                term_box.label(text=summary)
                row = term_box.row(align=True)
                edit = row.operator("halo.animation_term_edit", text="编辑", icon="GREASEPENCIL")
                edit.index = index
                up = row.operator("halo.animation_term_move", text="", icon="TRIA_UP")
                up.index = index
                up.direction = -1
                down = row.operator("halo.animation_term_move", text="", icon="TRIA_DOWN")
                down.index = index
                down.direction = 1
                remove = row.operator("halo.animation_term_remove", text="", icon="TRASH")
                remove.index = index
            row = term_box.row(align=True)
            row.operator("halo.animation_term_add", text="添加动画项", icon="ADD")
            open_resident = row.operator("halo.open_animation_json", text="多行 JSON", icon="TEXT")
            open_resident.target = "resident"
            item = next((entry for entry in project.definitions if entry.definition_id == project.active_definition), None)
            if item is not None:
                box = layout.box()
                box.label(text="过渡动画")
                for target, label, payload in (
                    ("startup", "启动 startup", item.startup_json),
                    ("shutdown", "关闭 shutdown", item.shutdown_json),
                ):
                    try:
                        parsed = json.loads(payload or "{}")
                        segment_count = len(parsed.get("segments", [])) if isinstance(parsed, dict) else 0
                        override_count = len(parsed.get("id_overrides", {})) if isinstance(parsed, dict) and isinstance(parsed.get("id_overrides"), dict) else 0
                        description = f"{label}：{segment_count} 段 / {override_count} 个 ID 覆盖"
                    except (TypeError, ValueError):
                        description = f"{label}：JSON 无法解析"
                    row = box.row(align=True)
                    row.label(text=description, icon="ANIM")
                    open_transition = row.operator("halo.open_animation_json", text="多行编辑", icon="TEXT")
                    open_transition.target = target


    class HALO_PT_text_animation(Panel):
        bl_idname = "HALO_PT_text_animation"
        bl_label = "Halo 动画 JSON"
        bl_category = "Halo 光环"
        bl_space_type = "TEXT_EDITOR"
        bl_region_type = "UI"

        @classmethod
        def poll(cls, context):
            text = getattr(getattr(context, "space_data", None), "text", None)
            return text is not None and bool(text.get("halo_animation_editor"))

        def draw(self, context):
            layout = self.layout
            text = context.space_data.text
            labels = {"resident": "常驻动画", "startup": "启动过渡", "shutdown": "关闭过渡"}
            layout.label(text=labels.get(text.get("halo_animation_target"), "动画 JSON"), icon="ANIM")
            layout.label(text=text.get("halo_definition_id", ""))
            if text.get("halo_node_uuid"):
                layout.label(text=f"组 UUID: {text.get('halo_node_uuid')[:8]}")
            layout.operator("halo.apply_animation_json", text="应用动画 JSON", icon="CHECKMARK")
            layout.operator("halo.return_3d_view", text="返回 3D 视图", icon="VIEW3D")


    class HALO_PT_tree(Panel):
        bl_idname = "HALO_PT_tree"
        bl_label = "树形编辑"
        bl_category = "Halo 光环"
        bl_space_type = "VIEW_3D"
        bl_region_type = "UI"

        @classmethod
        def poll(cls, context):
            obj = context.active_object
            return obj is not None and obj.get("halo_role") == "group"

        def draw(self, context):
            layout = self.layout
            project = context.scene.halo_project
            obj = context.active_object
            layout.label(text="当前父级 UUID")
            layout.label(text=obj.parent.get("halo_uuid", "无") if obj.parent else "无")
            row = layout.row(align=True)
            op = row.operator("halo.reparent", text="移动到父级", icon="CONSTRAINT_BONE")
            op.preserve_world = project.preserve_world_on_reparent
            row.prop(project, "preserve_world_on_reparent", text="保持世界位置")
            layout.label(text="在目标操作器中填写目标父级 UUID")


    class HALO_PT_validate_export(Panel):
        bl_idname = "HALO_PT_validate_export"
        bl_label = "验证 / 导出"
        bl_category = "Halo 光环"
        bl_space_type = "VIEW_3D"
        bl_region_type = "UI"

        def draw(self, context):
            layout = self.layout
            project = context.scene.halo_project
            row = layout.row(align=True)
            row.operator("halo.validate", text="验证", icon="CHECKMARK")
            row.operator("halo.export_zip", text="导出 ZIP", icon="PACKAGE")
            row.operator("halo.export_pack", text="导出文件夹", icon="FILE_FOLDER")
            if project.validation_json:
                try:
                    result = __import__("json").loads(project.validation_json)
                    errors = result.get("errors", [])
                    warnings = result.get("warnings", [])
                    layout.label(text=f"错误 {len(errors)} · 警告 {len(warnings)}", icon="ERROR" if errors else "INFO")
                    for message in errors[:3]:
                        layout.label(text=str(message), icon="ERROR")
                    for message in warnings[:3]:
                        layout.label(text=str(message), icon="ERROR")
                except Exception:
                    layout.label(text="验证结果 JSON 无法读取", icon="ERROR")


    PANEL_CLASSES = (
        HALO_UL_definitions,
        HALO_PT_project,
        HALO_PT_definition,
        HALO_PT_node,
        HALO_PT_animation,
        HALO_PT_text_animation,
        HALO_PT_tree,
        HALO_PT_validate_export,
    )

else:  # pragma: no cover
    PANEL_CLASSES = ()


def register_panels():
    if bpy is None:
        return
    for cls in PANEL_CLASSES:
        bpy.utils.register_class(cls)


def unregister_panels():
    if bpy is None:
        return
    for cls in reversed(PANEL_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass


__all__ = ["PANEL_CLASSES", "register_panels", "unregister_panels"]
