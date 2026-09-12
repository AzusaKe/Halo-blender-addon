"""Blender PropertyGroups used by the Halo Pack Editor.

All editable values have a JSON counterpart in ``raw_json``.  The property
groups intentionally contain both typed convenience fields and the original
document so an editor can work at either level without losing forward
compatibility with a newer Halo schema.
"""

from __future__ import annotations

import json

try:
    import bpy
    from bpy.props import (
        BoolProperty,
        CollectionProperty,
        EnumProperty,
        FloatProperty,
        FloatVectorProperty,
        IntProperty,
        PointerProperty,
        StringProperty,
    )
except ImportError:  # pragma: no cover - Blender-only module
    bpy = None


PREVIEW_SPACE_ITEMS = (
    ("HALO_LOCAL", "光环局部坐标", "在定义原点预览光环"),
    ("MC_HEAD", "MC 玩家头部", "将光环放到标准 Minecraft 玩家头部锚点"),
)
PREVIEW_MODE_ITEMS = (
    ("IDLE", "常驻动画", "播放常驻/循环动画"),
    ("STARTUP", "启动过渡", "预览 startup 过渡"),
    ("SHUTDOWN", "关闭过渡", "预览 shutdown 过渡"),
    ("SEQUENCE", "完整序列", "启动、常驻和关闭的完整序列"),
)
TRANSFORM_PRECISION_ITEMS = (
    ("COARSE", "粗调", "使用较大的位置、旋转和缩放步长"),
    ("FINE", "细调", "使用较小的精细步长"),
)
NODE_ROLE_ITEMS = (
    ("group", "部件组", "可拥有子组和图元的层级节点"),
    ("primitive", "图元", "Billboard、Ring 或 Mesh 几何图元"),
)
FUNCTION_ITEMS = (
    ("sin", "sin", "正弦项"),
    ("cos", "cos", "余弦项"),
    ("linear", "linear", "线性项"),
)
ANIMATION_CHANNEL_ITEMS = tuple((value, value, value) for value in (
    "offset.x", "offset.y", "offset.z",
    "rotation.yaw", "rotation.pitch", "rotation.roll",
    "scale.x", "scale.y", "scale.z", "alpha", "glow",
))
EASING_ITEMS = (
    ("linear", "Linear", "匀速插值"),
    ("ease_out_cubic", "Ease Out Cubic", "快速开始，末尾减速"),
    ("ease_in_out_cubic", "Ease In Out Cubic", "慢速开始和结束，中段加速"),
)

TRANSITION_DEFAULT_GROUP = "__HALO_DEFAULT__"
_TRANSITION_GROUP_ITEMS_CACHE = []


def _scene_preview_update(self, context):
    """Refresh root/head placement after a preview setting changes."""

    try:
        from .blender_scene import update_preview_roots
        if context and context.scene:
            update_preview_roots(context.scene)
    except Exception:
        # Property updates also run while files are being loaded, before all
        # addon modules are ready.  The frame handler will catch up later.
        pass


def _transition_selection_update(self, context):
    """Reset the selected row when switching transition/group timelines."""

    try:
        self.transition_segment_index = 0
    except (AttributeError, TypeError):
        pass


def _transition_group_items(self, context):
    """Return the default timeline plus every authored group ID in the tree."""

    del _TRANSITION_GROUP_ITEMS_CACHE[:]
    _TRANSITION_GROUP_ITEMS_CACHE.append((
        TRANSITION_DEFAULT_GROUP,
        "默认（光环根与未覆盖组）",
        "顶层 segments；同时用于光环根及没有独立 ID 覆盖的组",
    ))
    if bpy is None:
        return _TRANSITION_GROUP_ITEMS_CACHE
    definition_id = str(getattr(self, "active_definition", ""))
    seen = set()
    scene = getattr(context, "scene", None)
    for obj in (scene.objects if scene is not None else bpy.data.objects):
        if obj.get("halo_role") != "group" or obj.get("halo_definition_id") != definition_id:
            continue
        node = getattr(obj, "halo_node", None)
        group_id = str(getattr(node, "node_id", "") or "").strip()
        if not group_id or group_id in seen:
            continue
        seen.add(group_id)
        _TRANSITION_GROUP_ITEMS_CACHE.append((group_id, group_id, f"编辑组 ID {group_id} 的 id_overrides"))
    _TRANSITION_GROUP_ITEMS_CACHE[1:] = sorted(_TRANSITION_GROUP_ITEMS_CACHE[1:], key=lambda item: item[0].casefold())
    stored = str(self.get("transition_group_id", TRANSITION_DEFAULT_GROUP))
    if stored and stored != TRANSITION_DEFAULT_GROUP and stored not in seen:
        _TRANSITION_GROUP_ITEMS_CACHE.append((stored, f"{stored}（当前定义中未找到）", "保留 JSON 中的旧 ID 覆盖"))
    return _TRANSITION_GROUP_ITEMS_CACHE


def _active_definition_index_update(self, context):
    """Keep the string ID used by operators in sync with the UIList index."""

    try:
        if 0 <= int(self.active_definition_index) < len(self.definitions):
            self.active_definition = self.definitions[int(self.active_definition_index)].definition_id
    except (AttributeError, IndexError, TypeError, ValueError):
        pass


def _definition_id_update(self, context):
    """Rename a definition and every scene/UI reference as one operation."""

    if self.get("halo_definition_id_update_guard"):
        return
    new_id = str(self.definition_id).strip()
    old_id = str(self.get("halo_previous_definition_id", ""))
    scene = getattr(self, "id_data", None)
    project = getattr(scene, "halo_project", None)
    root_uuid = str(getattr(self, "root_uuid", ""))
    root_hint = None
    scene_objects = scene.objects if scene is not None else ()
    if bpy is not None and root_uuid:
        root_hint = next((obj for obj in scene_objects if obj.get("halo_uuid") == root_uuid), None)
        if not old_id and root_hint is not None:
            old_id = str(root_hint.get("halo_definition_id", ""))
    if not new_id:
        if old_id:
            self["halo_definition_id_update_guard"] = True
            try:
                self.definition_id = old_id
            finally:
                self.pop("halo_definition_id_update_guard", None)
        if project is not None:
            project["halo_definition_id_error"] = "光环 ID 不能为空"
        return
    if project is not None:
        duplicate = next((
            item for item in project.definitions
            if item.as_pointer() != self.as_pointer() and item.definition_id == new_id
        ), None)
        if duplicate is not None:
            self["halo_definition_id_update_guard"] = True
            try:
                self.definition_id = old_id
            finally:
                self.pop("halo_definition_id_update_guard", None)
            project["halo_definition_id_error"] = f"光环 ID 已存在：{new_id}"
            return
        project.pop("halo_definition_id_error", None)

    root = root_hint
    if bpy is not None and root is None and old_id:
        root = next((obj for obj in scene_objects if obj.get("halo_role") == "definition_root"
                     and obj.get("halo_definition_id") == old_id), None)
    migrated_primitives = []
    new_namespace = new_id.split(":", 1)[0] if ":" in new_id else "minecraft"
    old_namespace = old_id.split(":", 1)[0] if ":" in old_id else "minecraft"
    if root is not None and old_namespace != new_namespace:
        # RNA has already assigned the new ID. Temporarily expose the old ID
        # under the recursion guard so cache recovery and scene sync still
        # resolve exactly this source/definition during copy-on-write migration.
        self["halo_definition_id_update_guard"] = True
        try:
            self.definition_id = old_id
            from .texture_migration import migrate_definition_textures
            migrated_primitives, notices = migrate_definition_textures(scene, self, old_id, new_namespace)
            self.definition_id = new_id
            if project is not None:
                project["halo_definition_rename_notices"] = json.dumps(notices, ensure_ascii=False)
        except Exception as exc:
            self.definition_id = old_id
            if project is not None:
                project["halo_definition_id_error"] = f"贴图迁移失败，已保留原 ID：{exc}"
            return
        finally:
            self.pop("halo_definition_id_update_guard", None)
    elif project is not None:
        project.pop("halo_definition_rename_notices", None)

    self["halo_previous_definition_id"] = new_id
    self.namespace = new_namespace
    try:
        raw = json.loads(self.raw_json or "{}")
    except (TypeError, ValueError):
        raw = {}
    if isinstance(raw, dict):
        raw["id"] = new_id
        self.raw_json = json.dumps(raw, ensure_ascii=False, indent=2)

    if bpy is None:
        return
    root = root_hint
    if root is None and old_id:
        root = next((obj for obj in scene_objects if obj.get("halo_role") == "definition_root" and obj.get("halo_definition_id") == old_id), None)
    scene_old_id = str(root.get("halo_definition_id", old_id)) if root is not None else old_id
    if root is not None:
        try:
            root_raw = json.loads(root.get("halo_raw_json", "{}"))
        except (TypeError, ValueError):
            root_raw = {}
        if isinstance(root_raw, dict):
            root_raw["id"] = new_id
            root["halo_raw_json"] = json.dumps(root_raw, ensure_ascii=False, indent=2)
        root.name = f"Halo · {new_id}"
    if scene_old_id:
        for obj in scene_objects:
            if obj.get("halo_definition_id") != scene_old_id:
                continue
            obj["halo_definition_id"] = new_id
            node = getattr(obj, "halo_node", None)
            if node is not None:
                node.definition_id = new_id
        # Keep an open multi-line animation editor attached across ID and
        # namespace renames, including edits not applied yet.
        for text in bpy.data.texts:
            if (text.get("halo_animation_editor")
                    and text.get("halo_definition_id") == scene_old_id
                    and text.get("halo_scene_name", scene.name) == scene.name):
                text["halo_definition_id"] = new_id
    if project is not None:
        selected_index = int(getattr(project, "active_definition_index", -1))
        selected_item = project.definitions[selected_index] if 0 <= selected_index < len(project.definitions) else None
        if project.active_definition in {scene_old_id, old_id, new_id} or selected_item == self:
            project.active_definition = new_id
    if migrated_primitives:
        try:
            from .texture_migration import refresh_migrated_materials
            refresh_migrated_materials(scene, migrated_primitives)
        except Exception as exc:
            if project is not None:
                project["halo_definition_id_error"] = f"命名空间及贴图已迁移，预览刷新失败：{exc}"


def _definition_damping_update(self, context):
    """Write damping panel edits into both lossless definition JSON copies."""

    if self.get("halo_damping_update_guard"):
        return
    self["halo_damping_update_guard"] = True
    try:
        try:
            raw = json.loads(self.raw_json or "{}")
        except (TypeError, ValueError):
            raw = {}
        if not isinstance(raw, dict):
            raw = {}
        damping = raw.get("damping")
        damping = dict(damping) if isinstance(damping, dict) else {}
        damping.update({
            "linearFactor": float(self.damping_linear_factor),
            "angularFactor": float(self.damping_angular_factor),
            "maxLinearDistance": float(self.damping_max_linear),
            "maxAngularDegrees": float(self.damping_max_angular),
            "angularMomentumFactor": float(self.damping_angular_momentum_factor),
            "maxAngularMomentumDegrees": float(self.damping_max_angular_momentum),
        })
        raw["damping"] = damping
        raw["allow_angular_momentum"] = bool(self.allow_angular_momentum)
        self.raw_json = json.dumps(raw, ensure_ascii=False, indent=2)

        if bpy is not None:
            scene = getattr(self, "id_data", None)
            for obj in (scene.objects if scene is not None else bpy.data.objects):
                if obj.get("halo_role") != "definition_root":
                    continue
                if self.root_uuid and obj.get("halo_uuid") != self.root_uuid:
                    continue
                if not self.root_uuid and obj.get("halo_definition_id") != self.definition_id:
                    continue
                try:
                    root_raw = json.loads(obj.get("halo_raw_json", "{}"))
                except (TypeError, ValueError):
                    root_raw = {}
                if not isinstance(root_raw, dict):
                    root_raw = {}
                root_damping = root_raw.get("damping")
                root_damping = dict(root_damping) if isinstance(root_damping, dict) else {}
                root_damping.update(damping)
                root_raw["damping"] = root_damping
                root_raw["allow_angular_momentum"] = bool(self.allow_angular_momentum)
                obj["halo_raw_json"] = json.dumps(root_raw, ensure_ascii=False, indent=2)
                break
    finally:
        self.pop("halo_damping_update_guard", None)


def _definition_visibility_update(self, context):
    """Show or hide every preview object belonging to one definition."""

    if bpy is None:
        return
    visible = bool(getattr(self, "visible", True))
    definition_id = str(getattr(self, "definition_id", ""))
    root_uuid = str(getattr(self, "root_uuid", ""))
    scene = getattr(self, "id_data", None)
    for obj in (scene.objects if scene is not None else bpy.data.objects):
        belongs = obj.get("halo_definition_id") == definition_id
        if root_uuid and obj.get("halo_role") == "definition_root":
            belongs = obj.get("halo_uuid") == root_uuid
        if not belongs:
            continue
        obj.hide_viewport = not visible
        obj.hide_render = not visible
        try:
            obj.hide_set(not visible)
        except (RuntimeError, TypeError):
            pass


def _node_transform_update(self, context):
    """Apply typed MC transform fields to the owning Blender object."""

    obj = getattr(self, "id_data", None)
    if obj is None or getattr(obj, "get", lambda *_: None)("halo_role") != "group":
        return
    if obj.get("halo_property_update_guard"):
        return
    try:
        from .geometry import mc_rotation_quaternion, mc_to_blender
        obj["halo_property_update_guard"] = True
        obj.location = mc_to_blender(self.position)
        obj.rotation_mode = "QUATERNION"
        quat = mc_rotation_quaternion(self.rotation)
        if quat is not None:
            obj.rotation_quaternion = quat
        value = float(self.scale)
        obj.scale = (value, value, value)
        try:
            raw = json.loads(obj.get("halo_raw_json", "{}"))
        except (TypeError, ValueError):
            raw = {}
        if isinstance(raw, dict):
            raw["position"] = [float(value) for value in self.position]
            raw["rotation"] = [float(value) for value in self.rotation]
            raw["scale"] = float(self.scale)
            obj["halo_raw_json"] = json.dumps(raw, ensure_ascii=False, separators=(",", ":"))
    finally:
        if obj is not None:
            obj.pop("halo_property_update_guard", None)


def _node_group_update(self, context):
    """Apply group metadata immediately and refresh the current preview."""

    obj = getattr(self, "id_data", None)
    if obj is None or getattr(obj, "get", lambda *_: None)("halo_role") != "group":
        return
    if obj.get("halo_property_update_guard"):
        return
    try:
        raw = json.loads(obj.get("halo_raw_json", "{}"))
    except (TypeError, ValueError):
        raw = {}
    if isinstance(raw, dict):
        node_id = str(self.node_id).strip()
        if node_id:
            raw["id"] = node_id
        else:
            raw.pop("id", None)
        raw["glowing"] = bool(self.glowing)
        raw["inherit_alpha"] = bool(self.inherit_alpha)
        raw["inherit_glow"] = bool(self.inherit_glow)
        obj["halo_raw_json"] = json.dumps(raw, ensure_ascii=False, separators=(",", ":"))
    try:
        from .handlers import update_animation
        if context and context.scene:
            update_animation(context.scene)
    except Exception:
        pass


def _primitive_geometry_update(self, context):
    """Rebuild primitive geometry/materials after a typed field changes."""

    obj = getattr(self, "id_data", None)
    if obj is None or getattr(obj, "get", lambda *_: None)("halo_role") != "primitive":
        return
    if obj.get("halo_property_update_guard"):
        return
    try:
        from .operators import _set_node_mesh
        obj["halo_property_update_guard"] = True
        obj["halo_face_camera"] = bool(self.face_camera)
        _set_node_mesh(obj)
    except Exception:
        # A partially loaded file can invoke updates before the object's mesh
        # or the extension operators are available.  Manual refresh remains
        # available as a recovery path.
        pass
    finally:
        obj.pop("halo_property_update_guard", None)


def _primitive_face_camera_update(self, context):
    obj = getattr(self, "id_data", None)
    if obj is None or getattr(obj, "get", lambda *_: None)("halo_role") != "primitive":
        return
    if obj.get("halo_property_update_guard"):
        return
    try:
        from .operators import _set_node_mesh
        obj["halo_property_update_guard"] = True
        # Recreate the primitive from its typed fields and restore identity.
        # This discards the temporary camera-facing viewport rotation when the
        # option is disabled and gives "强制刷新" the same repair semantics.
        _set_node_mesh(obj)
    except Exception:
        obj["halo_face_camera"] = bool(self.face_camera)
    finally:
        obj.pop("halo_property_update_guard", None)
    try:
        if bool(self.face_camera) and context and context.scene:
            from .handlers import update_face_camera
            update_face_camera(context.scene)
    except Exception:
        pass


if bpy is not None:

    class HaloPackSourcePG(bpy.types.PropertyGroup):
        source_id: StringProperty(name="来源 ID", default="", options={"HIDDEN"})
        name: StringProperty(name="名称", default="资源包")
        source_kind: EnumProperty(
            name="类型",
            items=(
                ("ZIP", "ZIP", "压缩资源包"),
                ("FOLDER", "文件夹", "解包资源包目录"),
                ("LOCAL", "本地编辑", "新建光环和导入贴图使用的独立工作缓存"),
            ),
            default="ZIP",
        )
        source_path: StringProperty(name="来源路径", default="", subtype="FILE_PATH")
        pack_root: StringProperty(name="缓存目录", default="", subtype="DIR_PATH", options={"HIDDEN"})
        definition_count: IntProperty(name="光环数", default=0, min=0)

    class HaloAnimationTermPG(bpy.types.PropertyGroup):
        function: EnumProperty(name="函数", items=FUNCTION_ITEMS, default="sin")
        amplitude: FloatProperty(name="A", default=0.0)
        omega: FloatProperty(name="omega", default=0.0)
        phi: FloatProperty(name="phi", default=0.0)
        start: FloatProperty(name="start", default=0.0)
        speed: FloatProperty(name="speed", default=0.0)
        raw_json: StringProperty(name="原始 JSON", default="", options={"HIDDEN"})


    class HaloTransitionPropertyPG(bpy.types.PropertyGroup):
        enabled: BoolProperty(name="启用", default=False)
        from_x: FloatProperty(name="From X", default=0.0)
        from_y: FloatProperty(name="From Y", default=0.0)
        from_z: FloatProperty(name="From Z", default=0.0)
        to_x: FloatProperty(name="To X", default=0.0)
        to_y: FloatProperty(name="To Y", default=0.0)
        to_z: FloatProperty(name="To Z", default=0.0)
        scalar_from: FloatProperty(name="From", default=0.0)
        scalar_to: FloatProperty(name="To", default=0.0)
        property_duration: FloatProperty(name="属性时长", default=0.0, min=0.0)
        property_easing: EnumProperty(name="属性缓动", items=EASING_ITEMS, default="linear")
        degrees_x: FloatProperty(name="Degrees X", default=0.0)
        degrees_y: FloatProperty(name="Degrees Y", default=0.0)
        degrees_z: FloatProperty(name="Degrees Z", default=0.0)
        has_from: BoolProperty(name="有 from", default=False)
        has_to: BoolProperty(name="有 to", default=False)
        has_degrees: BoolProperty(name="有 degrees", default=False)
        raw_json: StringProperty(name="原始 JSON", default="", options={"HIDDEN"})


    class HaloTransitionSegmentPG(bpy.types.PropertyGroup):
        duration: FloatProperty(name="持续时间", default=1.0, min=0.0)
        easing: EnumProperty(name="缓动", items=EASING_ITEMS, default="linear")
        offset: PointerProperty(type=HaloTransitionPropertyPG)
        scale: PointerProperty(type=HaloTransitionPropertyPG)
        alpha: PointerProperty(type=HaloTransitionPropertyPG)
        rotation: PointerProperty(type=HaloTransitionPropertyPG)
        raw_json: StringProperty(name="原始 JSON", default="", options={"HIDDEN"})


    class HaloNodePG(bpy.types.PropertyGroup):
        uuid: StringProperty(name="UUID", default="", options={"HIDDEN"})
        definition_id: StringProperty(name="光环 ID", default="", options={"HIDDEN"})
        role: EnumProperty(name="节点类型", items=NODE_ROLE_ITEMS, default="group")
        node_id: StringProperty(name="部件 ID", default="", update=_node_group_update)
        primitive_type: EnumProperty(
            name="图元类型",
            items=(
                ("billboard", "Billboard", "水平四边形"),
                ("ring", "Ring", "圆环/圆柱"),
                ("mesh", "Mesh", "Halo 2.0 OBJ 网格"),
            ),
            default="billboard",
            update=_primitive_geometry_update,
        )
        position: FloatVectorProperty(name="位置", size=3, default=(0.0, 0.0, 0.0), precision=5, update=_node_transform_update)
        rotation: FloatVectorProperty(name="旋转 YXZ", size=3, default=(0.0, 0.0, 0.0), precision=4, update=_node_transform_update)
        scale: FloatProperty(name="缩放", default=1.0, precision=5, update=_node_transform_update)
        glowing: BoolProperty(name="发光", default=True, update=_node_group_update)
        inherit_alpha: BoolProperty(name="继承 Alpha", default=True, update=_node_group_update)
        inherit_glow: BoolProperty(name="继承 Glow", default=True, update=_node_group_update)
        texture: StringProperty(name="纹理", default="", update=_primitive_geometry_update)
        inner_texture: StringProperty(name="内侧纹理", default="", update=_primitive_geometry_update)
        size: FloatVectorProperty(name="尺寸", size=2, default=(1.0, 1.0), min=0.0, precision=5, update=_primitive_geometry_update)
        mesh_model: StringProperty(name="OBJ 模型", default="", update=_primitive_geometry_update)
        mesh_size: FloatVectorProperty(
            name="Mesh 尺寸", size=3, default=(1.0, 1.0, 1.0), min=0.0, precision=5,
            description="按 OBJ 已引用顶点的包围盒逐轴缩放；保持 OBJ 原点不变",
            update=_primitive_geometry_update,
        )
        mesh_preserve_proportions: BoolProperty(
            name="保持原始比例",
            description="保留 OBJ 原始坐标比例并忽略目标包围盒尺寸；1 OBJ 单位对应 1 格",
            default=False,
            update=_primitive_geometry_update,
        )
        mesh_scale: FloatProperty(
            name="统一缩放",
            description="保持原始比例时，围绕 OBJ 原点统一缩放 XYZ；仅在开关开启时生效",
            default=1.0,
            min=0.0,
            precision=5,
            update=_primitive_geometry_update,
        )
        mesh_double_sided: BoolProperty(name="双面", default=True, update=_primitive_geometry_update)
        mesh_mask_enabled: BoolProperty(name="启用 Alpha Mask", default=False, update=_primitive_geometry_update)
        mesh_mask_texture: StringProperty(name="遮罩纹理", default="", update=_primitive_geometry_update)
        mesh_mask_mode: EnumProperty(
            name="遮罩模式",
            items=(("linear", "Linear", "遮罩红色通道直接乘 Alpha"), ("step", "Step", "红色通道达到阈值时显示")),
            default="linear",
            update=_primitive_geometry_update,
        )
        mesh_mask_threshold: FloatProperty(
            name="Step 阈值", default=0.5, min=0.0, max=1.0, precision=4,
            update=_primitive_geometry_update,
        )
        segments: IntProperty(name="分段数", default=32, min=3, max=4096, update=_primitive_geometry_update)
        face_camera: BoolProperty(name="面向相机", default=False, update=_primitive_face_camera_update)
        animation_json: StringProperty(name="常驻动画 JSON", default="")
        raw_json: StringProperty(name="原始 JSON", default="", options={"HIDDEN"})
        parent_uuid: StringProperty(name="父级 UUID", default="", options={"HIDDEN"})

        animation_terms: CollectionProperty(type=HaloAnimationTermPG)


    class HaloDefinitionPG(bpy.types.PropertyGroup):
        definition_id: StringProperty(name="Definition ID", default="", update=_definition_id_update)
        namespace: StringProperty(name="命名空间", default="minecraft")
        source_path: StringProperty(name="JSON 文件", default="", subtype="FILE_PATH")
        source_id: StringProperty(name="资源包来源", default="", options={"HIDDEN"})
        visible: BoolProperty(name="在预览中显示", default=True, update=_definition_visibility_update)
        raw_json: StringProperty(name="完整 JSON", default="", options={"HIDDEN"})
        schema_version: StringProperty(name="Schema", default="1.1.0")
        orientation_mode: EnumProperty(
            name="朝向模式",
            items=(
                ("locked", "Locked", "保持定义朝向"),
                ("free", "Free", "随头部朝向"),
                ("sync", "Sync", "带同步偏移"),
            ),
            default="locked", update=_scene_preview_update,
        )
        sync_offset: FloatVectorProperty(name="同步偏移", size=3, default=(0.0, 0.0, 0.0), precision=4, update=_scene_preview_update)
        positioning_offset: FloatVectorProperty(name="头部偏移", size=3, default=(0.0, 0.0, 0.0), precision=5, update=_scene_preview_update)
        positioning_scale: FloatProperty(name="整体缩放", default=1.0, precision=5, update=_scene_preview_update)
        allow_angular_momentum: BoolProperty(name="允许角动量", default=False, update=_definition_damping_update)
        hide_on_sleep: BoolProperty(name="睡眠时隐藏", default=False)
        display_in_invisible: BoolProperty(name="隐形时显示", default=False)
        damping_linear_factor: FloatProperty(
            name="线性阻尼系数", description="linearFactor；通常为 0 到 1，越接近 1 越快贴近目标位置，越接近 0 跟随越慢",
            default=0.15, soft_min=0.0, soft_max=1.0, precision=5, update=_definition_damping_update,
        )
        damping_angular_factor: FloatProperty(
            name="角度阻尼系数", description="angularFactor；通常为 0 到 1，越接近 1 越快贴近目标朝向，越接近 0 跟随越慢",
            default=0.1, soft_min=0.0, soft_max=1.0, precision=5, update=_definition_damping_update,
        )
        damping_max_linear: FloatProperty(
            name="最大线性偏移", description="maxLinearDistance；单位为 Minecraft 方块",
            default=3.0, min=0.0, precision=5, update=_definition_damping_update,
        )
        damping_max_angular: FloatProperty(
            name="最大角度偏移", description="maxAngularDegrees；单位为度",
            default=180.0, min=0.0, precision=4, update=_definition_damping_update,
        )
        damping_angular_momentum_factor: FloatProperty(
            name="角动量响应系数", description="angularMomentumFactor；通常为 0 到 1，越接近 1 越快响应目标朝向，越接近 0 响应越慢",
            default=0.3, soft_min=0.0, soft_max=1.0, precision=5, update=_definition_damping_update,
        )
        damping_max_angular_momentum: FloatProperty(
            name="最大角动量偏角", description="maxAngularMomentumDegrees；单位为度",
            default=45.0, min=0.0, precision=4, update=_definition_damping_update,
        )
        animation_json: StringProperty(name="常驻动画 JSON", default="")
        startup_json: StringProperty(name="启动动画 JSON", default="")
        shutdown_json: StringProperty(name="关闭动画 JSON", default="")
        root_uuid: StringProperty(name="根对象 UUID", default="", options={"HIDDEN"})
        expanded: BoolProperty(name="展开", default=True)


    class HaloProjectPG(bpy.types.PropertyGroup):
        pack_root: StringProperty(name="资源包目录", default="", subtype="DIR_PATH")
        source_path: StringProperty(name="导入源", default="", subtype="FILE_PATH")
        manifest_json: StringProperty(name="pack.mcmeta", default="")
        schema_version: StringProperty(name="默认 Schema", default="1.1.0")
        output_path: StringProperty(name="导出路径", default="", subtype="FILE_PATH")
        active_definition: StringProperty(name="当前光环", default="")
        active_definition_index: IntProperty(name="当前光环索引", default=0, min=0, options={"HIDDEN"}, update=_active_definition_index_update)
        active_source_index: IntProperty(name="当前来源索引", default=0, min=0, options={"HIDDEN"})
        active_uuid: StringProperty(name="当前部件", default="")
        preview_space: EnumProperty(name="预览坐标系", items=PREVIEW_SPACE_ITEMS, default="HALO_LOCAL", update=_scene_preview_update)
        preview_mode: EnumProperty(name="动画模式", items=PREVIEW_MODE_ITEMS, default="IDLE")
        preview_fps: FloatProperty(name="预览 FPS", default=20.0, min=1.0, max=240.0)
        preview_phase: FloatProperty(name="触发相位", default=0.0, min=0.0, max=1.0)
        transition_duration: FloatProperty(name="过渡时长", default=1.0, min=0.0)
        animation_channel: EnumProperty(name="动画通道", items=ANIMATION_CHANNEL_ITEMS, default="offset.x")
        animation_term_index: IntProperty(name="动画项索引", default=0, min=0)
        mesh_mask_axis: EnumProperty(
            name="遮罩 UV 轴",
            items=(("u", "U", "编辑 U 方向循环偏移"), ("v", "V", "编辑 V 方向循环偏移")),
            default="u",
        )
        mesh_mask_term_index: IntProperty(name="遮罩动画项索引", default=0, min=0)
        transition_target: EnumProperty(
            name="过渡目标",
            items=(("startup", "启动", ""), ("shutdown", "关闭", "")),
            default="startup",
            update=_transition_selection_update,
        )
        transition_group_id: EnumProperty(
            name="组 ID",
            items=_transition_group_items,
            update=_transition_selection_update,
        )
        transition_segment_index: IntProperty(name="过渡段索引", default=0, min=0)
        head_yaw: FloatProperty(name="头部 Yaw", default=0.0, update=_scene_preview_update)
        head_pitch: FloatProperty(name="头部 Pitch", default=0.0, update=_scene_preview_update)
        head_roll: FloatProperty(name="头部 Roll", default=0.0, update=_scene_preview_update)
        show_head: BoolProperty(name="显示玩家头部", default=True, update=_scene_preview_update)
        neutral_environment: FloatProperty(name="非发光亮度", default=0.25, min=0.0, max=1.0)
        preserve_world_on_reparent: BoolProperty(name="重设父级保持世界位置", default=True)
        transform_precision: EnumProperty(name="变换精度", items=TRANSFORM_PRECISION_ITEMS, default="FINE")
        coarse_position_step: FloatProperty(name="粗调位置步长", default=0.1, min=0.00001, precision=5)
        coarse_rotation_step: FloatProperty(name="粗调旋转步长", default=5.0, min=0.0001, precision=4)
        coarse_scale_step: FloatProperty(name="粗调缩放步长", default=0.1, min=0.00001, precision=5)
        fine_position_step: FloatProperty(name="细调位置步长", default=0.01, min=0.00001, precision=5)
        fine_rotation_step: FloatProperty(name="细调旋转步长", default=0.5, min=0.0001, precision=4)
        fine_scale_step: FloatProperty(name="细调缩放步长", default=0.01, min=0.00001, precision=5)
        raw_text_name: StringProperty(name="JSON 文本块", default="", options={"HIDDEN"})
        animation_text_name: StringProperty(name="动画 JSON 文本块", default="", options={"HIDDEN"})
        validation_json: StringProperty(name="验证结果", default="")
        mesh_conversion_active: BoolProperty(name="Mesh 转换进行中", default=False, options={"HIDDEN", "SKIP_SAVE"})
        mesh_conversion_progress: FloatProperty(
            name="转换进度", default=0.0, min=0.0, max=1.0, subtype="FACTOR", options={"HIDDEN", "SKIP_SAVE"},
        )
        mesh_conversion_completed: IntProperty(name="已完成面簇", default=0, min=0, options={"HIDDEN", "SKIP_SAVE"})
        mesh_conversion_total: IntProperty(name="面簇总数", default=0, min=0, options={"HIDDEN", "SKIP_SAVE"})
        mesh_conversion_status: StringProperty(name="转换状态", default="", options={"HIDDEN", "SKIP_SAVE"})
        mesh_conversion_cancel_requested: BoolProperty(
            name="请求取消 Mesh 转换", default=False, options={"HIDDEN", "SKIP_SAVE"},
        )
        definitions: CollectionProperty(type=HaloDefinitionPG)
        sources: CollectionProperty(type=HaloPackSourcePG)


    PROPERTY_CLASSES = (
        HaloPackSourcePG,
        HaloAnimationTermPG,
        HaloTransitionPropertyPG,
        HaloTransitionSegmentPG,
        HaloNodePG,
        HaloDefinitionPG,
        HaloProjectPG,
    )

else:  # pragma: no cover - enables importing source for py_compile/documentation
    class HaloPackSourcePG:  # type: ignore[no-redef]
        pass

    class HaloAnimationTermPG:  # type: ignore[no-redef]
        pass

    class HaloTransitionPropertyPG:  # type: ignore[no-redef]
        pass

    class HaloTransitionSegmentPG:  # type: ignore[no-redef]
        pass

    class HaloNodePG:  # type: ignore[no-redef]
        pass

    class HaloDefinitionPG:  # type: ignore[no-redef]
        pass

    class HaloProjectPG:  # type: ignore[no-redef]
        pass

    PROPERTY_CLASSES = ()


def register_properties():
    if bpy is None:  # pragma: no cover
        return
    for cls in PROPERTY_CLASSES:
        bpy.utils.register_class(cls)
    if not hasattr(bpy.types.Scene, "halo_project"):
        bpy.types.Scene.halo_project = PointerProperty(type=HaloProjectPG)
    if not hasattr(bpy.types.Object, "halo_node"):
        bpy.types.Object.halo_node = PointerProperty(type=HaloNodePG)


def unregister_properties():
    if bpy is None:  # pragma: no cover
        return
    if hasattr(bpy.types.Object, "halo_node"):
        del bpy.types.Object.halo_node
    if hasattr(bpy.types.Scene, "halo_project"):
        del bpy.types.Scene.halo_project
    for cls in reversed(PROPERTY_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass


__all__ = [
    "PREVIEW_SPACE_ITEMS",
    "PREVIEW_MODE_ITEMS",
    "PROPERTY_CLASSES",
    "HaloPackSourcePG",
    "HaloAnimationTermPG",
    "HaloTransitionPropertyPG",
    "HaloTransitionSegmentPG",
    "HaloNodePG",
    "HaloDefinitionPG",
    "HaloProjectPG",
    "register_properties",
    "unregister_properties",
]
