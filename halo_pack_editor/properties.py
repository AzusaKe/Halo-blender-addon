"""Blender PropertyGroups used by the Halo Pack Editor.

All editable values have a JSON counterpart in ``raw_json``.  The property
groups intentionally contain both typed convenience fields and the original
document so an editor can work at either level without losing forward
compatibility with a newer Halo schema.
"""

from __future__ import annotations

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
NODE_ROLE_ITEMS = (
    ("group", "部件组", "可拥有子组和图元的层级节点"),
    ("primitive", "图元", "Billboard 或 Ring 几何图元"),
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
EASING_ITEMS = tuple((value, value, value) for value in ("linear", "ease_in", "ease_out", "ease_in_out"))


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


def _active_definition_index_update(self, context):
    """Keep the string ID used by operators in sync with the UIList index."""

    try:
        if 0 <= int(self.active_definition_index) < len(self.definitions):
            self.active_definition = self.definitions[int(self.active_definition_index)].definition_id
    except (AttributeError, IndexError, TypeError, ValueError):
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
    finally:
        if obj is not None:
            obj.pop("halo_property_update_guard", None)


if bpy is not None:

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
        node_id: StringProperty(name="部件 ID", default="")
        primitive_type: EnumProperty(
            name="图元类型",
            items=(("billboard", "Billboard", "水平四边形"), ("ring", "Ring", "圆环/圆柱")),
            default="billboard",
        )
        position: FloatVectorProperty(name="位置", size=3, default=(0.0, 0.0, 0.0), precision=5, update=_node_transform_update)
        rotation: FloatVectorProperty(name="旋转 YXZ", size=3, default=(0.0, 0.0, 0.0), precision=4, update=_node_transform_update)
        scale: FloatProperty(name="缩放", default=1.0, precision=5, update=_node_transform_update)
        glowing: BoolProperty(name="发光", default=True)
        inherit_alpha: BoolProperty(name="继承 Alpha", default=True)
        inherit_glow: BoolProperty(name="继承 Glow", default=True)
        texture: StringProperty(name="纹理", default="")
        inner_texture: StringProperty(name="内侧纹理", default="")
        size: FloatVectorProperty(name="尺寸", size=2, default=(1.0, 1.0), min=0.0, precision=5)
        segments: IntProperty(name="分段数", default=32, min=3, max=4096)
        face_camera: BoolProperty(name="面向相机", default=False)
        animation_json: StringProperty(name="常驻动画 JSON", default="")
        raw_json: StringProperty(name="原始 JSON", default="", options={"HIDDEN"})
        parent_uuid: StringProperty(name="父级 UUID", default="", options={"HIDDEN"})

        animation_terms: CollectionProperty(type=HaloAnimationTermPG)


    class HaloDefinitionPG(bpy.types.PropertyGroup):
        definition_id: StringProperty(name="Definition ID", default="")
        namespace: StringProperty(name="命名空间", default="minecraft")
        source_path: StringProperty(name="JSON 文件", default="", subtype="FILE_PATH")
        raw_json: StringProperty(name="完整 JSON", default="", options={"HIDDEN"})
        schema_version: StringProperty(name="Schema", default="1.0.10")
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
        allow_angular_momentum: BoolProperty(name="允许角动量", default=False)
        hide_on_sleep: BoolProperty(name="睡眠时隐藏", default=False)
        display_in_invisible: BoolProperty(name="隐形时显示", default=False)
        damping_linear_factor: FloatProperty(name="线性阻尼", default=0.15)
        damping_angular_factor: FloatProperty(name="角阻尼", default=0.1)
        damping_max_linear: FloatProperty(name="最大线性距离", default=3.0, min=0.0)
        damping_max_angular: FloatProperty(name="最大角度", default=180.0, min=0.0)
        damping_angular_momentum_factor: FloatProperty(name="角动量因子", default=0.3)
        damping_max_angular_momentum: FloatProperty(name="最大角动量角度", default=45.0, min=0.0)
        animation_json: StringProperty(name="常驻动画 JSON", default="")
        startup_json: StringProperty(name="启动动画 JSON", default="")
        shutdown_json: StringProperty(name="关闭动画 JSON", default="")
        root_uuid: StringProperty(name="根对象 UUID", default="", options={"HIDDEN"})
        expanded: BoolProperty(name="展开", default=True)


    class HaloProjectPG(bpy.types.PropertyGroup):
        pack_root: StringProperty(name="资源包目录", default="", subtype="DIR_PATH")
        source_path: StringProperty(name="导入源", default="", subtype="FILE_PATH")
        manifest_json: StringProperty(name="pack.mcmeta", default="")
        schema_version: StringProperty(name="默认 Schema", default="1.0.10")
        output_path: StringProperty(name="导出路径", default="", subtype="FILE_PATH")
        active_definition: StringProperty(name="当前光环", default="")
        active_definition_index: IntProperty(name="当前光环索引", default=0, min=0, options={"HIDDEN"}, update=_active_definition_index_update)
        active_uuid: StringProperty(name="当前部件", default="")
        preview_space: EnumProperty(name="预览坐标系", items=PREVIEW_SPACE_ITEMS, default="HALO_LOCAL", update=_scene_preview_update)
        preview_mode: EnumProperty(name="动画模式", items=PREVIEW_MODE_ITEMS, default="IDLE")
        preview_fps: FloatProperty(name="预览 FPS", default=20.0, min=1.0, max=240.0)
        preview_phase: FloatProperty(name="触发相位", default=0.0, min=0.0, max=1.0)
        transition_duration: FloatProperty(name="过渡时长", default=1.0, min=0.0)
        animation_channel: EnumProperty(name="动画通道", items=ANIMATION_CHANNEL_ITEMS, default="offset.x")
        animation_term_index: IntProperty(name="动画项索引", default=0, min=0)
        transition_target: EnumProperty(name="过渡目标", items=(("startup", "启动", ""), ("shutdown", "关闭", "")), default="startup")
        transition_segment_index: IntProperty(name="过渡段索引", default=0, min=0)
        head_yaw: FloatProperty(name="头部 Yaw", default=0.0, update=_scene_preview_update)
        head_pitch: FloatProperty(name="头部 Pitch", default=0.0, update=_scene_preview_update)
        head_roll: FloatProperty(name="头部 Roll", default=0.0, update=_scene_preview_update)
        show_head: BoolProperty(name="显示玩家头部", default=True, update=_scene_preview_update)
        neutral_environment: FloatProperty(name="非发光亮度", default=0.25, min=0.0, max=1.0)
        preserve_world_on_reparent: BoolProperty(name="重设父级保持世界位置", default=True)
        raw_text_name: StringProperty(name="JSON 文本块", default="", options={"HIDDEN"})
        validation_json: StringProperty(name="验证结果", default="")
        definitions: CollectionProperty(type=HaloDefinitionPG)


    PROPERTY_CLASSES = (
        HaloAnimationTermPG,
        HaloTransitionPropertyPG,
        HaloTransitionSegmentPG,
        HaloNodePG,
        HaloDefinitionPG,
        HaloProjectPG,
    )

else:  # pragma: no cover - enables importing source for py_compile/documentation
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
    "HaloAnimationTermPG",
    "HaloTransitionPropertyPG",
    "HaloTransitionSegmentPG",
    "HaloNodePG",
    "HaloDefinitionPG",
    "HaloProjectPG",
    "register_properties",
    "unregister_properties",
]
