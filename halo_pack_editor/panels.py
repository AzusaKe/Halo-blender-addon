"""Chinese N-panel and Outliner-friendly controls for the Halo editor."""

from __future__ import annotations

import json

from .properties import TRANSITION_DEFAULT_GROUP

try:
    import bpy
    from bpy.types import Panel, UIList
except ImportError:  # pragma: no cover
    bpy = None


def _transition_panel_segments(document, group_id):
    """Return authored segments and whether this ID has its own override."""

    if not isinstance(document, dict):
        return [], False
    if not group_id or group_id == TRANSITION_DEFAULT_GROUP:
        segments = document.get("segments")
        return (segments if isinstance(segments, list) else []), True
    overrides = document.get("id_overrides")
    if not isinstance(overrides, dict) or group_id not in overrides:
        return [], False
    entry = overrides[group_id]
    if isinstance(entry, list):
        return entry, True
    if isinstance(entry, dict) and isinstance(entry.get("segments"), list):
        return entry["segments"], True
    return [], True


def _transition_endpoint_text(value):
    if value is None:
        return "—"
    if isinstance(value, list):
        return "[" + ", ".join(f"{float(item):g}" for item in value[:3]) + "]"
    try:
        return f"{float(value):g}"
    except (TypeError, ValueError):
        return str(value)


def _transition_property(segment, channel):
    if not isinstance(segment, dict):
        return None
    value = segment.get(channel)
    if channel == "alpha" and not isinstance(value, dict):
        value = segment.get("opacity")
    return value if isinstance(value, dict) else None


def _definition_primitive_count(scene, definition_id):
    """Count every managed primitive belonging to one Halo definition."""

    if scene is None or not definition_id:
        return 0
    return sum(
        1
        for obj in scene.objects
        if obj.get("halo_role") == "primitive" and obj.get("halo_definition_id") == definition_id
    )


def _draw_labpbr_controls(layout, context, obj, node):
    """Draw base-first labPBR sidecar import controls for one primitive."""

    from . import blender_scene
    from .materials import labpbr_texture_id, resolve_texture_path

    box = layout.box()
    box.label(text="labPBR 1.3", icon="MATERIAL")
    base_texture_id = str(getattr(node, "texture", "") or "").strip()
    pack_root = blender_scene.definition_pack_root(
        context.scene, str(obj.get("halo_definition_id", ""))
    )
    generated_available = bool(base_texture_id and bpy is not None and any(
        image.get("halo_texture_id") == base_texture_id
        and image.get("halo_generated_texture")
        and not image.get("halo_missing_texture")
        for image in bpy.data.images
    ))
    base_available = bool(
        base_texture_id and (resolve_texture_path(base_texture_id, pack_root) or generated_available)
    )
    if not base_available:
        box.label(text="请先导入有效的固有色贴图", icon="ERROR")
    for kind, label, target in (
        ("NORMAL", "法线贴图", "NORMAL"),
        ("SPECULAR", "高光贴图", "SPECULAR"),
    ):
        row = box.row(align=True)
        sidecar_id = labpbr_texture_id(base_texture_id, kind) if base_texture_id else ""
        available = bool(sidecar_id and resolve_texture_path(sidecar_id, pack_root))
        row.label(text=f"{label}：{'已导入' if available else '未导入'}", icon="CHECKMARK" if available else "IMAGE_DATA")
        action = row.row(align=True)
        action.enabled = base_available
        operator = action.operator(
            "halo.import_texture",
            text="替换" if available else "导入",
            icon="FILE_REFRESH" if available else "IMPORT",
        )
        operator.target = target
        if available:
            box.label(text=sidecar_id)
    box.label(text="导入后会关闭同一资源中所有使用组的自发光", icon="INFO")


def _mesh_mask_terms_for_panel(obj, axis):
    try:
        raw = json.loads(obj.get("halo_primitive_raw_json", obj.get("halo_raw_json", "{}")))
    except (TypeError, ValueError):
        return []
    material = raw.get("material") if isinstance(raw, dict) else None
    effects = material.get("effects") if isinstance(material, dict) else None
    if not isinstance(effects, list):
        return []
    mask = next((value for value in effects if isinstance(value, dict)
                 and str(value.get("type", "")) == "alpha_mask"), None)
    uv = mask.get("uv_offset") if isinstance(mask, dict) else None
    terms = uv.get(axis) if isinstance(uv, dict) else None
    return terms if isinstance(terms, list) else []


def _term_summary(term):
    function = str(term.get("function", "linear")) if isinstance(term, dict) else "?"
    def number(key, fallback=0):
        try:
            return f"{float(term.get(key, fallback)):g}"
        except (AttributeError, TypeError, ValueError):
            return str(term.get(key, fallback)) if isinstance(term, dict) else "?"
    if function in {"sin", "cos"}:
        amplitude = number("A", term.get("amplitude", 0))
        return f"{function}: A={amplitude}, ω={number('omega')}, φ={number('phi')}"
    return f"{function}: start={number('start')}, speed={number('speed')}"


if bpy is not None:

    def _nudge_button(row, target, axis, direction, text):
        operator = row.operator("halo.nudge_transform", text=text)
        operator.target = target
        operator.axis = axis
        operator.direction = direction


    def _draw_transform_editor(layout, node, project):
        row = layout.row(align=True)
        row.prop_enum(project, "transform_precision", "COARSE", text="粗调")
        row.prop_enum(project, "transform_precision", "FINE", text="细调")
        prefix = "coarse" if project.transform_precision == "COARSE" else "fine"
        steps = layout.column(align=True)
        steps.prop(project, f"{prefix}_position_step", text="位置步长")
        steps.prop(project, f"{prefix}_rotation_step", text="旋转步长")
        steps.prop(project, f"{prefix}_scale_step", text="缩放步长")
        for target, label, property_name in (
            ("position", "位置", "position"),
            ("rotation", "旋转 YXZ", "rotation"),
        ):
            layout.label(text=label)
            for axis, axis_name in enumerate(("X", "Y", "Z")):
                row = layout.row(align=True)
                row.label(text=axis_name)
                _nudge_button(row, target, axis, -1, "−")
                row.prop(node, property_name, index=axis, text="")
                _nudge_button(row, target, axis, 1, "+")
        row = layout.row(align=True)
        row.label(text="统一缩放")
        _nudge_button(row, "scale", 0, -1, "−")
        row.prop(node, "scale", text="")
        _nudge_button(row, "scale", 0, 1, "+")

    class HALO_UL_sources(UIList):
        bl_idname = "HALO_UL_sources"

        def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
            row = layout.row(align=True)
            icon_name = "PACKAGE" if item.source_kind == "ZIP" else "FILE_FOLDER" if item.source_kind == "FOLDER" else "GREASEPENCIL"
            row.label(text=item.name or "未命名来源", icon=icon_name)
            row.label(text=f"{item.definition_count} 个光环")


    class HALO_UL_definitions(UIList):
        bl_idname = "HALO_UL_definitions"

        def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
            row = layout.row(align=True)
            icon_name = "OUTLINER_OB_EMPTY" if item.definition_id else "QUESTION"
            row.label(text=item.definition_id or "未命名光环", icon=icon_name)
            if item.schema_version:
                row.label(text=item.schema_version)
            row.prop(
                item,
                "visible",
                text="",
                icon="HIDE_OFF" if item.visible else "HIDE_ON",
                emboss=False,
            )


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
            box.label(text="资源包来源（ZIP / 文件夹 / 本地编辑）")
            box.template_list("HALO_UL_sources", "sources", project, "sources", project, "active_source_index", rows=3)
            source_row = box.row(align=True)
            source_row.operator("halo.remove_source", text="清除所选来源", icon="TRASH")
            source_row.enabled = bool(project.sources)
            if project.sources and 0 <= project.active_source_index < len(project.sources):
                source = project.sources[project.active_source_index]
                box.label(text=source.source_path, icon="FILE_FOLDER")
            box.label(text="合并包元数据")
            box.prop(project, "manifest_json", text="pack.mcmeta")
            layout.template_list("HALO_UL_definitions", "definitions", project, "definitions", project, "active_definition_index", rows=3)
            row = layout.row(align=True)
            row.operator("halo.new_definition", text="新建光环", icon="ADD")
            remove = row.row(align=True)
            remove.enabled = bool(project.definitions)
            remove.operator("halo.remove_definition", text="清除光环", icon="TRASH")
            row = layout.row(align=True)
            row.operator("halo.open_raw_json", text="打开 JSON", icon="TEXT")
            row.operator("halo.apply_raw_json", text="应用 JSON", icon="FILE_REFRESH")
            layout.operator("halo.pack_resources", text="修复并内嵌资源", icon="PACKAGE")
            layout.label(text="保存 .blend 时自动内嵌 Halo 资源", icon="INFO")
            cleaned = json.loads(project.get("halo_temp_cleanup_json", "[]"))
            if cleaned:
                layout.label(text=f"上次清理失效 Temp 图片：{len(cleaned)} 个", icon="INFO")
            warnings = json.loads(project.get("halo_resource_warnings", "[]"))
            if warnings:
                warning_box = layout.box()
                warning_box.alert = True
                warning_box.label(text=f"{len(warnings)} 项资源未能完整恢复", icon="ERROR")
                warning_box.label(text="点击修复查看详情；缺失 PNG 请重新链接")
            layout.label(text="组的父子关系：见下方“树形编辑”面板", icon="INFO")


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
            layout.prop(item, "visible", text="在预览中显示")
            error = context.scene.halo_project.get("halo_definition_id_error", "")
            if error:
                layout.label(text=error, icon="ERROR")
            for notice in json.loads(context.scene.halo_project.get("halo_definition_rename_notices", "[]")):
                layout.label(text=notice, icon="INFO")
            layout.label(
                text=f"图元总数：{_definition_primitive_count(context.scene, item.definition_id)}",
                icon="MESH_DATA",
            )
            layout.prop(item, "schema_version", text="Schema")
            layout.prop(item, "orientation_mode", text="朝向")
            layout.prop(item, "sync_offset", text="同步偏移")
            box = layout.box()
            box.label(text="定位（Minecraft 坐标）")
            box.prop(item, "positioning_offset", text="头部偏移")
            box.prop(item, "positioning_scale", text="整体缩放")
            box = layout.box()
            box.label(text="阻尼跟踪（仅编辑，暂不模拟）", icon="TRACKING")
            box.prop(item, "damping_linear_factor", text="线性阻尼系数")
            box.prop(item, "damping_angular_factor", text="角度阻尼系数")
            box.prop(item, "damping_max_linear", text="最大线性偏移（方块）")
            box.prop(item, "damping_max_angular", text="最大角度偏移（°）")
            box.separator()
            box.prop(item, "allow_angular_momentum", text="允许角动量")
            momentum = box.column(align=True)
            momentum.enabled = bool(item.allow_angular_momentum)
            momentum.prop(item, "damping_angular_momentum_factor", text="角动量响应系数")
            momentum.prop(item, "damping_max_angular_momentum", text="最大角动量偏角（°）")
            box = layout.box()
            box.label(text="状态")
            box.prop(item, "hide_on_sleep")
            box.prop(item, "display_in_invisible")
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
            project = context.scene.halo_project
            if project.mesh_conversion_active or project.mesh_conversion_status:
                progress_box = layout.box()
                progress_box.label(
                    text="Mesh 转换任务" if project.mesh_conversion_active else "上次 Mesh 转换",
                    icon="TIME" if project.mesh_conversion_active else "CHECKMARK",
                )
                progress_row = progress_box.row()
                progress_row.enabled = False
                progress_row.prop(
                    project,
                    "mesh_conversion_progress",
                    text=f"{project.mesh_conversion_progress * 100.0:.0f}%",
                    slider=True,
                )
                progress_box.label(text=project.mesh_conversion_status or "等待进度更新")
                if project.mesh_conversion_active:
                    if project.mesh_conversion_total:
                        progress_box.label(
                            text=f"面簇 {project.mesh_conversion_completed}/{project.mesh_conversion_total}"
                        )
                    progress_box.operator("halo.cancel_mesh_conversion", text="取消转换", icon="CANCEL")
                    progress_box.label(text="可切换窗口；当前单张烘焙完成后响应取消", icon="INFO")
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
            if role == "group":
                layout.prop(node, "node_id", text="部件 ID")
                layout.label(text=f"UUID: {node.uuid}")
                transform = layout.box()
                transform.label(text="局部变换（Minecraft 坐标）", icon="ORIENTATION_LOCAL")
                transform.label(text="视图 G/R/S 已锁定，请使用下方控件", icon="LOCKED")
                _draw_transform_editor(transform, node, context.scene.halo_project)
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
                layout.label(text=f"UUID: {node.uuid}")
                parent = obj.parent if obj.parent is not None and obj.parent.get("halo_role") == "group" else None
                transform = layout.box()
                transform.label(text="所属部件组变换", icon="OUTLINER_OB_EMPTY")
                if parent is not None and getattr(parent, "halo_node", None) is not None:
                    parent_node = parent.halo_node
                    transform.label(text=parent_node.node_id or parent.name)
                    transform.label(text="视图 G/R/S 已锁定，请使用下方控件", icon="LOCKED")
                    _draw_transform_editor(transform, parent_node, context.scene.halo_project)
                    transform.operator("halo.select_parent_group", text="在视图中选择此部件组", icon="RESTRICT_SELECT_OFF")
                    if len([child for child in parent.children if child.get("halo_role") == "primitive"]) > 1:
                        transform.label(text="同组图元共享此变换", icon="INFO")
                else:
                    transform.label(text="图元缺少有效父组", icon="ERROR")
                box = layout.box()
                box.prop(node, "primitive_type", text="类型")
                if node.primitive_type == "ring":
                    box.prop(node, "texture", text="外侧纹理")
                    outer_row = box.row(align=True)
                    outer_import = outer_row.operator("halo.import_texture", text="导入外侧 PNG", icon="IMAGE_DATA")
                    outer_import.target = "OUTER"
                    box.prop(node, "inner_texture", text="内侧纹理")
                    inner_row = box.row(align=True)
                    inner_import = inner_row.operator("halo.import_texture", text="导入内侧 PNG", icon="IMAGE_DATA")
                    inner_import.target = "INNER"
                    inner_row.operator("halo.clear_inner_texture", text="使用外侧", icon="X")
                    box.prop(node, "segments", text="分段")
                    box.prop(node, "size", text="尺寸")
                elif node.primitive_type == "mesh":
                    box.prop(node, "mesh_model", text="OBJ 模型")
                    model_row = box.row(align=True)
                    model_row.operator("halo.import_mesh_model", text="外部 OBJ", icon="IMPORT")
                    model_row.operator("halo.import_scene_mesh", text="项目 Mesh…", icon="MESH_DATA")
                    box.prop(node, "texture", text="主纹理")
                    texture_import = box.operator("halo.import_texture", text="导入主纹理 PNG", icon="IMAGE_DATA")
                    texture_import.target = "OUTER"
                    box.prop(node, "mesh_preserve_proportions", text="保持原始比例")
                    if node.mesh_preserve_proportions:
                        box.prop(node, "mesh_scale", text="统一缩放")
                        box.label(text="scale=1 时，1 OBJ 单位对应 1 格", icon="INFO")
                    else:
                        box.prop(node, "mesh_size", text="目标包围盒尺寸")
                    box.prop(node, "mesh_double_sided", text="双面材质")
                    shader = box.box()
                    shader.label(text="Mesh 简易 Shader", icon="SHADING_RENDERED")
                    shader.prop(node, "mesh_mask_enabled", text="启用 Alpha Mask")
                    if node.mesh_mask_enabled:
                        shader.prop(node, "mesh_mask_texture", text="遮罩纹理")
                        mask_import = shader.operator("halo.import_texture", text="导入遮罩 PNG", icon="IMAGE_DATA")
                        mask_import.target = "MASK"
                        shader.prop(node, "mesh_mask_mode", text="遮罩模式")
                        shader.prop(node, "mesh_mask_threshold", text="Step 阈值")
                        shader.label(text="UV 循环偏移动画")
                        axis_row = shader.row(align=True)
                        axis_row.prop_enum(context.scene.halo_project, "mesh_mask_axis", "u", text="U")
                        axis_row.prop_enum(context.scene.halo_project, "mesh_mask_axis", "v", text="V")
                        terms = _mesh_mask_terms_for_panel(obj, context.scene.halo_project.mesh_mask_axis)
                        if not terms:
                            shader.label(text="此轴尚无动画项", icon="INFO")
                        for index, term in enumerate(terms):
                            row = shader.row(align=True)
                            row.label(text=f"{index + 1}. {_term_summary(term)}")
                            edit = row.operator("halo.mesh_mask_term_edit", text="", icon="GREASEPENCIL")
                            edit.index = index
                            up = row.operator("halo.mesh_mask_term_move", text="", icon="TRIA_UP")
                            up.index = index
                            up.direction = -1
                            down = row.operator("halo.mesh_mask_term_move", text="", icon="TRIA_DOWN")
                            down.index = index
                            down.direction = 1
                            remove = row.operator("halo.mesh_mask_term_remove", text="", icon="X")
                            remove.index = index
                        shader.operator("halo.mesh_mask_term_add", text="添加 UV 动画项", icon="ADD")
                else:
                    box.prop(node, "texture", text="纹理")
                    texture_import = box.operator("halo.import_texture", text="导入 PNG", icon="IMAGE_DATA")
                    texture_import.target = "OUTER"
                    box.prop(node, "size", text="尺寸")
                    box.prop(node, "face_camera", text="面向相机")
                _draw_labpbr_controls(box, context, obj, node)
                box.operator("halo.refresh_geometry", text="强制刷新", icon="FILE_REFRESH")
            try:
                from .operators import _selected_sibling_nodes
                selected_nodes = _selected_sibling_nodes(context)
                selection_error = ""
            except ValueError as exc:
                selected_nodes = []
                selection_error = str(exc)
            if len(context.selected_objects) > 1:
                selection_box = layout.box()
                if selected_nodes:
                    selection_box.label(text=f"已选择 {len(selected_nodes)} 个同级部件", icon="RESTRICT_SELECT_OFF")
                    selected_roles = {item.get("halo_role") for item in selected_nodes}
                    if len(selected_roles) > 1:
                        selection_box.label(text="复制/删除可混选；移动请按组或图元分别选择", icon="INFO")
                else:
                    selection_box.label(text=selection_error or "多选不符合批量操作条件", icon="ERROR")
            row = layout.row(align=True)
            count_suffix = f"（{len(selected_nodes)}）" if len(selected_nodes) > 1 else ""
            row.operator("halo.duplicate_node", text=f"复制所选{count_suffix}", icon="DUPLICATE")
            row.operator("halo.delete_node", text=f"删除所选{count_suffix}", icon="TRASH")
            if role == "primitive":
                move_text = "迁移所选图元到其他父级…" if len(selected_nodes) > 1 else "迁移到其他父级…"
                layout.operator("halo.move_primitive", text=move_text, icon="CONSTRAINT_BONE")


    class HALO_PT_mesh_conversion(Panel):
        bl_idname = "HALO_PT_mesh_conversion"
        bl_label = "Mesh 转子组"
        bl_category = "Halo 光环"
        bl_space_type = "VIEW_3D"
        bl_region_type = "UI"

        @classmethod
        def poll(cls, context):
            obj = context.active_object
            return obj is not None and obj.get("halo_role") in {"definition_root", "group", "primitive"}

        def draw(self, context):
            layout = self.layout
            obj = context.active_object
            target = obj.parent if obj.get("halo_role") == "primitive" else obj
            if target is not None and target.get("halo_role") == "definition_root":
                target_name = "光环根（顶层）"
            else:
                target_node = getattr(target, "halo_node", None) if target is not None else None
                target_name = str(getattr(target_node, "node_id", "") or getattr(target, "name", "无"))
            layout.label(text=f"目标父级：{target_name}", icon="OUTLINER_OB_EMPTY")
            layout.operator("halo.convert_mesh", text="导入 Mesh…", icon="MESH_DATA")
            layout.label(text="从当前场景 Collection 选择普通网格")


    class HALO_PT_animation(Panel):
        bl_idname = "HALO_PT_animation"
        bl_label = "动画预览"
        bl_category = "Halo 光环"
        bl_space_type = "VIEW_3D"
        bl_region_type = "UI"

        def draw(self, context):
            layout = self.layout
            project = context.scene.halo_project
            renderer = layout.box()
            if context.scene.render.engine == "BLENDER_EEVEE":
                renderer.label(text="预览渲染器：EEVEE（抖动透明）", icon="CHECKMARK")
                renderer.operator("halo.configure_eevee_preview", text="刷新 EEVEE 材质", icon="FILE_REFRESH")
            elif context.scene.render.engine == "CYCLES":
                renderer.label(text="预览渲染器：Cycles（单层双面）", icon="CHECKMARK")
                renderer.operator("halo.refresh_render_materials", text="刷新 Cycles 材质", icon="FILE_REFRESH")
            else:
                renderer.label(text="当前渲染器不保证 Halo 面剔除", icon="ERROR")
                renderer.operator("halo.configure_eevee_preview", text="切换到 EEVEE 一致性预览", icon="RENDER_STILL")
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
                row = box.row(align=True)
                row.prop_enum(project, "transition_target", "startup", text="启动 startup")
                row.prop_enum(project, "transition_target", "shutdown", text="关闭 shutdown")
                row = box.row(align=True)
                row.prop(project, "transition_group_id", text="组 ID")
                row.operator("halo.transition_use_active_group", text="", icon="EYEDROPPER")
                payload = item.startup_json if project.transition_target == "startup" else item.shutdown_json
                try:
                    transition = json.loads(payload or "{}")
                    if not isinstance(transition, dict):
                        raise ValueError("根节点不是对象")
                    parse_error = ""
                except (TypeError, ValueError) as exc:
                    transition = {}
                    parse_error = str(exc)
                if parse_error:
                    box.label(text=f"JSON 无法解析：{parse_error}", icon="ERROR")
                    open_transition = box.operator("halo.open_animation_json", text="打开多行 JSON 修复", icon="TEXT")
                    open_transition.target = project.transition_target
                else:
                    group_id = project.transition_group_id or TRANSITION_DEFAULT_GROUP
                    segments, has_override = _transition_panel_segments(transition, group_id)
                    default_segments = transition.get("segments") if isinstance(transition.get("segments"), list) else []
                    if group_id != TRANSITION_DEFAULT_GROUP and not has_override:
                        box.label(text=f"当前组继承默认时间线（{len(default_segments)} 段）", icon="LINKED")
                        box.label(text="添加过渡段会建立独立 id_overrides", icon="INFO")
                    elif group_id != TRANSITION_DEFAULT_GROUP:
                        row = box.row(align=True)
                        row.label(text=f"独立 ID 覆盖：{len(segments)} 段", icon="ANIM")
                        row.operator("halo.transition_override_clear", text="删除覆盖", icon="UNLINKED")
                    else:
                        box.label(text=f"默认时间线：{len(segments)} 段", icon="ANIM")

                    boundary_indices = {}
                    for channel in ("offset", "scale", "alpha", "rotation"):
                        active_indices = [
                            candidate_index for candidate_index, candidate in enumerate(segments)
                            if _transition_property(candidate, channel) is not None
                        ]
                        if active_indices:
                            boundary_indices[channel] = (
                                active_indices[0] if project.transition_target == "startup" else active_indices[-1]
                            )

                    for index, segment in enumerate(segments):
                        segment_box = box.box()
                        if not isinstance(segment, dict):
                            segment_box.label(text=f"段 {index + 1}：不是 JSON 对象", icon="ERROR")
                            continue
                        duration = segment.get("duration", "未填写")
                        easing = segment.get("easing", "linear")
                        header = segment_box.row(align=True)
                        header.label(text=f"段 {index + 1}   时间 {duration}s   {easing}", icon="KEYFRAME")
                        edit = header.operator("halo.transition_segment_edit", text="", icon="GREASEPENCIL")
                        edit.index = index
                        up = header.operator("halo.transition_segment_move", text="", icon="TRIA_UP")
                        up.index = index
                        up.direction = -1
                        down = header.operator("halo.transition_segment_move", text="", icon="TRIA_DOWN")
                        down.index = index
                        down.direction = 1
                        remove = header.operator("halo.transition_segment_remove", text="", icon="TRASH")
                        remove.index = index

                        for channel, label in (("offset", "Offset"), ("scale", "Scale"), ("alpha", "Alpha"), ("rotation", "Rotation YXZ")):
                            prop = _transition_property(segment, channel)
                            if prop is None:
                                continue
                            details = f"from {_transition_endpoint_text(prop.get('from'))}  →  to {_transition_endpoint_text(prop.get('to'))}"
                            overrides = []
                            if prop.get("duration") is not None:
                                overrides.append(f"{prop.get('duration')}s")
                            if prop.get("easing") is not None:
                                overrides.append(str(prop.get("easing")))
                            if channel == "rotation" and prop.get("degrees") is not None:
                                overrides.append(f"degrees {_transition_endpoint_text(prop.get('degrees'))}")
                            if overrides:
                                details += "  ·  " + " / ".join(overrides)
                            required_endpoint = "from" if project.transition_target == "startup" else "to"
                            boundary_missing = (
                                boundary_indices.get(channel) == index and prop.get(required_endpoint) is None
                            )
                            if boundary_missing:
                                details += f"  ·  缺少必填 {required_endpoint}"
                            channel_row = segment_box.row(align=True)
                            channel_row.alert = boundary_missing
                            channel_row.label(text=f"{label}: {details}")
                            edit_channel = channel_row.operator("halo.transition_channel_edit", text="", icon="GREASEPENCIL")
                            edit_channel.index = index
                            edit_channel.channel = channel
                            remove_channel = channel_row.operator("halo.transition_channel_remove", text="", icon="X")
                            remove_channel.index = index
                            remove_channel.channel = channel

                        add_row = segment_box.row(align=True)
                        for channel, label in (("offset", "+Offset"), ("scale", "+Scale"), ("alpha", "+Alpha"), ("rotation", "+Rotation")):
                            if _transition_property(segment, channel) is not None:
                                continue
                            add_channel = add_row.operator("halo.transition_channel_edit", text=label)
                            add_channel.index = index
                            add_channel.channel = channel

                    row = box.row(align=True)
                    row.operator("halo.transition_segment_add", text="添加过渡段", icon="ADD")
                    open_transition = row.operator("halo.open_animation_json", text="完整 JSON", icon="TEXT")
                    open_transition.target = project.transition_target


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
            from .animation_text import text_is_pending
            if text_is_pending(text):
                layout.label(text="有尚未应用的修改", icon="ERROR")
            else:
                layout.label(text="修改已同步", icon="CHECKMARK")
            error = text.get("halo_animation_last_error", "")
            if error:
                layout.label(text=error, icon="ERROR")
            layout.operator("halo.return_3d_view", text="应用并返回 3D 视图", icon="VIEW3D")


    class HALO_PT_tree(Panel):
        bl_idname = "HALO_PT_tree"
        bl_label = "树形编辑"
        bl_category = "Halo 光环"
        bl_space_type = "VIEW_3D"
        bl_region_type = "UI"

        def draw(self, context):
            layout = self.layout
            project = context.scene.halo_project
            obj = context.active_object
            role = obj.get("halo_role") if obj is not None else ""

            if role not in {"definition_root", "group", "primitive"}:
                layout.label(text="请先选择一个 Halo 部件组", icon="INFO")
                layout.label(text="可在“大纲视图”或 3D 视图中选择")
                return

            if role == "definition_root":
                layout.label(text="当前选择：光环根（不能移动）", icon="EMPTY_AXIS")
                layout.label(text="请选择它下面需要移动的部件组")
                return

            try:
                from .operators import _selected_sibling_nodes
                selected_nodes = _selected_sibling_nodes(context)
            except ValueError as exc:
                if len(context.selected_objects) > 1:
                    layout.label(text=str(exc), icon="ERROR")
                    layout.label(text="请重新选择同一父级下的部件", icon="INFO")
                    return
                selected_nodes = []

            if role == "primitive":
                parent = obj.parent if obj.parent is not None and obj.parent.get("halo_role") == "group" else None
                layout.label(text="当前选择：图元", icon="MESH_PLANE")
                if parent is None:
                    layout.label(text="此图元没有有效的所属组", icon="ERROR")
                else:
                    parent_node = getattr(parent, "halo_node", None)
                    parent_label = str(getattr(parent_node, "node_id", "") or parent.name)
                    layout.label(text=f"所属部件组：{parent_label}")
                    layout.operator("halo.select_parent_group", text="选择所属部件组", icon="RESTRICT_SELECT_OFF")
                    primitive_count = sum(1 for item in selected_nodes if item.get("halo_role") == "primitive")
                    if len(selected_nodes) > 1 and primitive_count == len(selected_nodes):
                        layout.label(text=f"已选择 {primitive_count} 个同组图元")
                    elif len(selected_nodes) > 1:
                        layout.label(text="移动图元时不能同时选择部件组", icon="ERROR")
                    move_text = "迁移所选图元到其他父级…" if primitive_count > 1 else "迁移到其他父级…"
                    layout.operator("halo.move_primitive", text=move_text, icon="CONSTRAINT_BONE")
                layout.label(text="Halo JSON 中只有组可以改变父级", icon="INFO")
                return

            parent = obj.parent
            if parent is None:
                parent_label = "无（非法层级）"
            elif parent.get("halo_role") == "definition_root":
                parent_label = "光环根（顶层）"
            else:
                parent_node = getattr(parent, "halo_node", None)
                parent_label = str(getattr(parent_node, "node_id", "") or parent.name)
            selected_group_count = sum(1 for item in selected_nodes if item.get("halo_role") == "group")
            if len(selected_nodes) > 1 and selected_group_count == len(selected_nodes):
                layout.label(text=f"已选择 {selected_group_count} 个同级组")
            elif len(selected_nodes) > 1:
                layout.label(text="移动组时不能同时选择图元", icon="ERROR")
            layout.label(text=f"当前父级：{parent_label}")
            row = layout.row(align=True)
            move_text = "为所选组选择新父级…" if selected_group_count > 1 else "选择新父级…"
            op = row.operator("halo.reparent", text=move_text, icon="CONSTRAINT_BONE")
            op.preserve_world = project.preserve_world_on_reparent
            row.prop(project, "preserve_world_on_reparent", text="保持世界位置")
            layout.label(text="列表会自动排除自身及其子组", icon="INFO")


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
        HALO_UL_sources,
        HALO_UL_definitions,
        HALO_PT_project,
        HALO_PT_definition,
        HALO_PT_node,
        HALO_PT_mesh_conversion,
        HALO_PT_tree,
        HALO_PT_animation,
        HALO_PT_text_animation,
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
