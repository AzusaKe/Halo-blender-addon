"""Frame-change animation preview for imported Halo definitions."""

from __future__ import annotations

import json
import math
from typing import Any, Mapping, Sequence

try:
    import bpy
    from bpy.app.handlers import persistent
except ImportError:  # pragma: no cover - Blender-only module
    bpy = None

from .geometry import mc_rotation_quaternion, mc_to_blender
from .materials import set_material_visual, set_mesh_mask_offset

_VIEW_DRAW_HANDLE = None
_SCENE_VIEW_QUATERNIONS: dict[int, tuple[float, float, float, float]] = {}

try:
    # The dependency-free core is the source of truth for the animation
    # clock, additive channels, inheritance and transition queues.  The
    # small JSON evaluator below remains as a graceful fallback for a .blend
    # saved with an older extension build.
    from .core.animation import (
        evaluate_definition_transition as _core_evaluate_transition,
        evaluate_definition_tree as _core_evaluate_tree,
        evaluate_resident as _core_evaluate_resident,
    )
    from .core.schema import parse_definition as _core_parse_definition
except (ImportError, AttributeError):  # pragma: no cover - fallback only
    _core_evaluate_transition = _core_evaluate_tree = _core_evaluate_resident = _core_parse_definition = None


def _number(value, default=0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def evaluate_term(term: Mapping[str, Any], time_seconds: float) -> float:
    """Evaluate one Halo ``sin``, ``cos`` or ``linear`` term."""

    function = str(term.get("function", "linear")).lower()
    if function == "sin":
        return _number(term.get("A", term.get("amplitude", 0.0))) * math.sin(
            _number(term.get("omega")) * math.pi * time_seconds + _number(term.get("phi"))
        )
    if function == "cos":
        return _number(term.get("A", term.get("amplitude", 0.0))) * math.cos(
            _number(term.get("omega")) * math.pi * time_seconds + _number(term.get("phi"))
        )
    if function == "linear":
        return _number(term.get("start")) + _number(term.get("speed")) * time_seconds
    return 0.0


def evaluate_terms(terms: Sequence[Mapping[str, Any]] | None, time_seconds: float, default: float = 0.0) -> float:
    return sum(evaluate_term(term, time_seconds) for term in (terms or ())) if terms else float(default)


def _apply_mesh_mask_animation(obj, time_seconds: float) -> None:
    node = getattr(obj, "halo_node", None)
    if node is None or getattr(node, "primitive_type", "") != "mesh":
        return
    raw = _load_json(obj.get("halo_primitive_raw_json", obj.get("halo_raw_json", "{}")))
    material = raw.get("material") if isinstance(raw, Mapping) else None
    effects = material.get("effects") if isinstance(material, Mapping) else None
    mask = next((value for value in effects or () if isinstance(value, Mapping)
                 and str(value.get("type", "")) == "alpha_mask"), None)
    uv = mask.get("uv_offset") if isinstance(mask, Mapping) else None
    u_terms = uv.get("u") if isinstance(uv, Mapping) and isinstance(uv.get("u"), list) else ()
    v_terms = uv.get("v") if isinstance(uv, Mapping) and isinstance(uv.get("v"), list) else ()
    u = evaluate_terms(u_terms, time_seconds, 0.0) % 1.0
    v = evaluate_terms(v_terms, time_seconds, 0.0) % 1.0
    for blender_material in getattr(obj.data, "materials", ()):
        set_mesh_mask_offset(blender_material, u, v)


def _channel(animation: Mapping[str, Any], group: str, axis: str, time_seconds: float, default=0.0) -> float:
    block = animation.get(group)
    if not isinstance(block, Mapping):
        return float(default)
    terms = block.get(axis)
    return evaluate_terms(terms if isinstance(terms, list) else (), time_seconds, default)


def evaluate_layer_animation(animation: Mapping[str, Any] | None, time_seconds: float) -> dict[str, Any]:
    """Evaluate all visual channels, matching the mod's additive semantics."""

    animation = animation if isinstance(animation, Mapping) else {}
    offset = [
        _channel(animation, "offset", axis, time_seconds)
        for axis in ("x", "y", "z")
    ]
    rotation = [
        _channel(animation, "rotation", axis, time_seconds)
        for axis in ("yaw", "pitch", "roll")
    ]
    # Halo scale animation is multiplicative around identity (1 + sum), while
    # offset/rotation are additive around zero.
    scale = [
        1.0 + _channel(animation, "scale", axis, time_seconds)
        for axis in ("x", "y", "z")
    ]
    alpha = max(0.0, min(1.0, evaluate_terms(animation.get("alpha"), time_seconds, 1.0)))
    glow = max(0.0, min(1.0, evaluate_terms(animation.get("glow"), time_seconds, 1.0)))
    return {"offset": offset, "rotation": rotation, "scale": scale, "alpha": alpha, "glow": glow}


def easing_value(name: str, progress: float) -> float:
    value = max(0.0, min(1.0, float(progress)))
    name = str(name or "linear").lower().replace("-", "_")
    if name in {"ease_out_cubic", "easeoutcubic"}:
        return 1.0 - (1.0 - value) ** 3
    if name in {"ease_in_out_cubic", "easeinoutcubic"}:
        if value < 0.5:
            return 4.0 * value ** 3
        return 1.0 - ((-2.0 * value + 2.0) ** 3) / 2.0
    return value


def _array(value, size: int, default: Sequence[float]) -> list[float]:
    if isinstance(value, (int, float)):
        values = [float(value)]
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        values = [_number(item) for item in value]
    else:
        values = []
    while len(values) < size:
        values.append(float(default[min(len(values), len(default) - 1)]))
    return values[:size]


def _transition_property(segment: Mapping[str, Any], property_name: str):
    value = segment.get(property_name)
    if value is None and property_name == "alpha":
        value = segment.get("opacity")
    return value if isinstance(value, Mapping) else None


def transition_duration(transition: Mapping[str, Any] | None) -> float:
    if not isinstance(transition, Mapping):
        return 0.0
    segments = transition.get("segments")
    if not isinstance(segments, list):
        return 0.0
    return sum(max(0.0, _number(segment.get("duration"))) for segment in segments if isinstance(segment, Mapping))


def evaluate_transition(
    transition: Mapping[str, Any] | None,
    time_seconds: float,
    property_name: str,
    component_count: int,
    steady_state: Sequence[float],
    *,
    is_shutdown: bool = False,
) -> list[float] | None:
    """Evaluate a startup/shutdown property at a transition-local time.

    Missing boundaries intentionally backfill to the supplied steady state,
    matching the Java deserializer's permissive behavior.
    """

    if not isinstance(transition, Mapping) or not isinstance(transition.get("segments"), list):
        return None
    segments = [segment for segment in transition["segments"] if isinstance(segment, Mapping)]
    if not segments:
        return None
    remaining = max(0.0, float(time_seconds))
    selected = segments[-1]
    local = 1.0
    for segment in segments:
        duration = max(0.0, _number(segment.get("duration")))
        if remaining <= duration or segment is segments[-1]:
            selected = segment
            local = 1.0 if duration <= 0.0 else max(0.0, min(1.0, remaining / duration))
            break
        remaining -= duration
    prop = _transition_property(selected, property_name)
    if prop is None:
        # A segment without this property carries the steady-state value.
        return list(steady_state[:component_count])
    default = list(steady_state[:component_count])
    start = _array(prop.get("from"), component_count, default)
    end = _array(prop.get("to"), component_count, default)
    if prop.get("from") is None:
        start = default[:]
    if prop.get("to") is None:
        end = default[:]
    duration = prop.get("duration")
    easing = prop.get("easing", selected.get("easing", "linear"))
    # The progress already uses segment duration.  Property-level duration is
    # an optional alternate clock, as in the mod parser.
    if duration is not None and _number(duration) > 0.0:
        segment_duration = max(0.000001, _number(selected.get("duration")))
        local = max(0.0, min(1.0, float(time_seconds) / _number(duration))) if float(time_seconds) <= _number(duration) else 1.0
    progress = easing_value(str(easing), local)
    degrees = _array(prop.get("degrees"), component_count, [0.0] * component_count) if property_name == "rotation" and prop.get("degrees") is not None else None
    values = []
    for index, (left, right) in enumerate(zip(start, end)):
        if degrees is not None and abs(degrees[index]) > 1e-9:
            minimum = abs(degrees[index])
            direction = 1.0 if degrees[index] >= 0.0 else -1.0
            target = right
            delta = target - left
            # Choose an equivalent turn that reaches the requested minimum
            # travel while retaining the requested direction.
            while abs(delta) < minimum:
                delta += direction * 360.0
            right = left + delta
        values.append(left + (right - left) * progress)
    return values


def _load_json(value, fallback=None):
    try:
        result = json.loads(value or "") if isinstance(value, str) else value
        return result if isinstance(result, Mapping) else (fallback if fallback is not None else {})
    except (TypeError, ValueError):
        return fallback if fallback is not None else {}


def _root_for(obj):
    current = obj
    while current is not None and current.get("halo_role") != "definition_root":
        current = current.parent
    return current


def _definition_raw(scene, root):
    item = next((entry for entry in scene.halo_project.definitions if entry.definition_id == root.get("halo_definition_id", "")), None)
    if item is not None:
        raw = dict(_load_json(item.raw_json, _load_json(root.get("halo_raw_json", "{}"))))
        for key, text in (("animation", item.animation_json), ("startup", item.startup_json), ("shutdown", item.shutdown_json)):
            parsed = _load_json(text, None)
            if isinstance(parsed, Mapping):
                raw[key] = dict(parsed)
    else:
        raw = dict(_load_json(root.get("halo_raw_json", "{}")))

    def overlay_group_animation(objects, groups):
        for obj, group in zip(objects, groups if isinstance(groups, list) else ()):
            if not isinstance(group, Mapping):
                continue
            node = obj.halo_node
            parsed = _load_json(getattr(node, "animation_json", "{}"), None)
            if isinstance(parsed, Mapping):
                group["animation"] = dict(parsed)
            # Typed panel values must affect the current frame immediately;
            # the definition-level raw JSON is synchronized lazily on export.
            group["glowing"] = bool(node.glowing)
            group["inherit_alpha"] = bool(node.inherit_alpha)
            group["inherit_glow"] = bool(node.inherit_glow)
            node_id = str(node.node_id).strip()
            if node_id:
                group["id"] = node_id
            else:
                group.pop("id", None)
            child_objects = sorted(
                (child for child in obj.children if child.get("halo_role") == "group"),
                key=lambda child: child.get("halo_path", child.name),
            )
            overlay_group_animation(child_objects, group.get("children", []))

    top_objects = sorted(
        (child for child in root.children if child.get("halo_role") == "group"),
        key=lambda child: child.get("halo_path", child.name),
    )
    overlay_group_animation(top_objects, raw.get("layers", []))
    return raw


def _transition_for_node(root_raw: Mapping[str, Any], root, node, mode: str, time_seconds: float, project):
    if mode == "IDLE":
        return None
    key = "startup" if mode == "STARTUP" else "shutdown"
    if mode == "SEQUENCE":
        startup = root_raw.get("startup") if isinstance(root_raw.get("startup"), Mapping) else {}
        shutdown = root_raw.get("shutdown") if isinstance(root_raw.get("shutdown"), Mapping) else {}
        startup_duration = transition_duration(startup)
        shutdown_duration = transition_duration(shutdown)
        idle_duration = max(0.0, _number(project.transition_duration, 1.0))
        if time_seconds < startup_duration:
            return startup, False, time_seconds
        if time_seconds < startup_duration + idle_duration:
            return None
        return shutdown, True, time_seconds - startup_duration - idle_duration
    transition = root_raw.get(key)
    if not isinstance(transition, Mapping):
        return None
    # id_overrides can be a direct array or {segments: [...]}.
    node_id = getattr(node, "get", lambda *_: "")("halo_id", "")
    raw_node = _load_json(node.get("halo_raw_json", "{}"))
    node_id = str(raw_node.get("id", node_id))
    overrides = transition.get("id_overrides")
    if isinstance(overrides, Mapping) and node_id in overrides:
        value = overrides[node_id]
        if isinstance(value, list):
            transition = dict(transition)
            transition["segments"] = value
        elif isinstance(value, Mapping):
            transition = dict(value)
    duration = transition_duration(transition)
    if duration <= 0.0:
        duration = max(0.000001, _number(project.transition_duration, 1.0))
    return transition, mode == "SHUTDOWN", min(float(time_seconds), duration)


def _set_delta(obj, value):
    obj.delta_location = mc_to_blender(value.get("offset", (0.0, 0.0, 0.0)))
    obj.delta_scale = tuple(value.get("scale", (1.0, 1.0, 1.0)))
    # Blender uses the object's rotation_mode for both the static and delta
    # rotation; there is no separate delta_rotation_mode property.
    obj.rotation_mode = "QUATERNION"
    quat = mc_rotation_quaternion(value.get("rotation", (0.0, 0.0, 0.0)))
    if quat is not None:
        obj.delta_rotation_quaternion = quat


def _set_root_local_animation(root, value):
    """Compose definition animation after the MC head/halo anchor transform.

    Minecraft's renderer applies ``T(anchor) R(anchor) S(positioning)`` first,
    followed by the definition animation's ``T R S``.  Blender's object delta
    location is not a local translation after the base rotation, so the root
    must be composed explicitly from the base pose captured by
    :func:`blender_scene.update_preview_roots`.
    """

    try:
        from mathutils import Quaternion, Vector
    except ImportError:  # pragma: no cover - Blender-only helper
        _set_delta(root, value)
        return

    base_location = Vector(root.get("halo_preview_base_location", tuple(root.location)))
    base_rotation = Quaternion(root.get("halo_preview_base_rotation", tuple(root.rotation_quaternion)))
    base_scale = Vector(root.get("halo_preview_base_scale", tuple(root.scale)))
    local_offset = Vector(mc_to_blender(value.get("offset", (0.0, 0.0, 0.0))))
    scaled_offset = Vector((
        local_offset.x * base_scale.x,
        local_offset.y * base_scale.y,
        local_offset.z * base_scale.z,
    ))
    animation_rotation = mc_rotation_quaternion(value.get("rotation", (0.0, 0.0, 0.0)))
    animation_scale = Vector(value.get("scale", (1.0, 1.0, 1.0)))

    root.location = base_location + base_rotation @ scaled_offset
    root.rotation_mode = "QUATERNION"
    root.rotation_quaternion = base_rotation @ (animation_rotation or Quaternion())
    root.scale = Vector((
        base_scale.x * animation_scale.x,
        base_scale.y * animation_scale.y,
        base_scale.z * animation_scale.z,
    ))

    # Older extension builds stored definition animation in delta transforms.
    # Clear those values so reopening an old .blend cannot apply it twice.
    root.delta_location = (0.0, 0.0, 0.0)
    root.delta_rotation_quaternion = (1.0, 0.0, 0.0, 0.0)
    root.delta_scale = (1.0, 1.0, 1.0)


def _apply_object_animation(scene, root, obj, idle_time: float, mode: str, transition_time: float, transition_data):
    raw = _load_json(obj.get("halo_animation_json", "{}"))
    evaluated = evaluate_layer_animation(raw, idle_time)
    if transition_data is not None:
        transition, shutdown, local_time = transition_data
        for key, count, steady in (
            ("offset", 3, (0.0, 0.0, 0.0)),
            ("scale", 3, (1.0, 1.0, 1.0)),
            ("rotation", 3, (0.0, 0.0, 0.0)),
            ("alpha", 1, (1.0,)),
        ):
            value = evaluate_transition(transition, local_time, key, count, steady, is_shutdown=shutdown)
            if value is None:
                continue
            if key == "alpha":
                evaluated["alpha"] = value[0]
            elif key == "offset":
                evaluated["offset"] = value
            elif key == "scale":
                evaluated["scale"] = value
            elif key == "rotation":
                evaluated["rotation"] = value
    if obj is root:
        _set_root_local_animation(root, evaluated)
    else:
        _set_delta(obj, evaluated)
    return evaluated


def _typed_group_pairs(blender_groups, typed_groups):
    """Yield matching Blender/typed groups in source-tree order."""

    for index, typed_group in enumerate(typed_groups or ()):
        if index >= len(blender_groups):
            break
        blender_group = blender_groups[index]
        yield blender_group, typed_group
        child_objects = [child for child in blender_group.children if child.get("halo_role") == "group"]
        child_objects.sort(key=lambda child: child.get("halo_path", child.name))
        yield from _typed_group_pairs(child_objects, getattr(typed_group, "children", ()))


def _core_scene_animation(scene, root, raw: Mapping[str, Any], time_seconds: float, mode: str) -> bool:
    """Evaluate one definition with the dependency-free core implementation."""

    if _core_parse_definition is None:
        return False
    try:
        definition = _core_parse_definition(
            raw,
            source_path=root.get("halo_source_path", ""),
            namespace=str(root.get("halo_definition_id", "minecraft:halo")).split(":", 1)[0],
        )
        project = scene.halo_project
        idle_time = project.preview_phase if mode != "IDLE" else time_seconds
        resident_root = _core_evaluate_resident(getattr(definition, "animation", None), idle_time)
        startup = getattr(definition, "startup", None)
        shutdown = getattr(definition, "shutdown", None)
        startup_duration = float(getattr(startup, "max_duration", 0.0) or 0.0)
        shutdown_duration = float(getattr(shutdown, "max_duration", 0.0) or 0.0)
        if mode == "SEQUENCE":
            idle_duration = max(0.0, _number(project.transition_duration, 1.0))
            if time_seconds < startup_duration:
                transition_kind, transition_time = True, time_seconds
            elif time_seconds < startup_duration + idle_duration:
                transition_kind, transition_time = None, 0.0
            else:
                transition_kind, transition_time = False, time_seconds - startup_duration - idle_duration
        elif mode == "STARTUP":
            transition_kind, transition_time = True, time_seconds
        elif mode == "SHUTDOWN":
            transition_kind, transition_time = False, time_seconds
        else:
            transition_kind, transition_time = None, 0.0
        root_state = dict(resident_root)
        if transition_kind is not None:
            transition = _core_evaluate_transition(definition, None, transition_time, startup=transition_kind)
            # TransitionResult intentionally has no glow channel; resident
            # glow remains active while alpha/transform transition.
            root_state.update({"offset": transition.offset, "scale": transition.scale, "rotation": transition.rotation, "alpha": transition.alpha})
        _set_root_local_animation(root, root_state)
        root["halo_preview_alpha"] = max(0.0, min(1.0, float(root_state.get("alpha", 1.0))))
        root["halo_preview_glow"] = max(0.0, min(1.0, float(root_state.get("glow", 1.0))))

        group_objects = [child for child in root.children if child.get("halo_role") == "group"]
        group_objects.sort(key=lambda child: child.get("halo_path", child.name))
        def visit(obj, typed_group, parent_alpha, parent_glow):
            state = dict(_core_evaluate_resident(getattr(typed_group, "animation", None), idle_time))
            if transition_kind is not None:
                transition = _core_evaluate_transition(
                    definition,
                    getattr(typed_group, "id", None),
                    transition_time,
                    startup=transition_kind,
                )
                state.update({"offset": transition.offset, "scale": transition.scale, "rotation": transition.rotation, "alpha": transition.alpha})
            _set_delta(obj, state)
            effective_alpha = max(0.0, min(1.0, float(state.get("alpha", 1.0)) * parent_alpha))
            effective_glow = max(0.0, min(1.0, float(state.get("glow", 1.0)) * parent_glow))
            obj["halo_preview_alpha"] = effective_alpha
            obj["halo_preview_glow"] = effective_glow
            visible_glow = effective_glow if bool(getattr(typed_group, "glowing", True)) else effective_glow * float(scene.halo_project.neutral_environment)
            for child in obj.children:
                if child.get("halo_role") == "primitive":
                    child["halo_preview_alpha"] = effective_alpha
                    child["halo_preview_glow"] = visible_glow
                    for material in getattr(child.data, "materials", ()):
                        set_material_visual(material, effective_alpha, visible_glow)
                    _apply_mesh_mask_animation(child, idle_time)
            next_alpha = effective_alpha if bool(getattr(typed_group, "inherit_alpha", True)) else 1.0
            next_glow = effective_glow if bool(getattr(typed_group, "inherit_glow", True)) else 1.0
            child_objects = [child for child in obj.children if child.get("halo_role") == "group"]
            child_objects.sort(key=lambda child: child.get("halo_path", child.name))
            for child_obj, child_group in zip(child_objects, getattr(typed_group, "children", ())):
                visit(child_obj, child_group, next_alpha, next_glow)

        for obj, typed_group in zip(group_objects, getattr(definition, "groups", ())):
            visit(obj, typed_group, root["halo_preview_alpha"], root["halo_preview_glow"])
        return True
    except Exception:
        return False


def _scene_key(scene) -> int:
    try:
        return int(scene.as_pointer())
    except (AttributeError, ReferenceError, TypeError, ValueError):
        return id(scene)


def _apply_face_camera_orientation(scene, world_quaternion, *, remember_view=False):
    """Apply one world-facing orientation after all parent animation."""

    if bpy is None or scene is None or world_quaternion is None:
        return
    try:
        from mathutils import Quaternion
        world_quaternion = Quaternion(world_quaternion)
    except (ImportError, TypeError, ValueError):
        return
    if remember_view:
        _SCENE_VIEW_QUATERNIONS[_scene_key(scene)] = tuple(float(value) for value in world_quaternion)
    for obj in scene.objects:
        if obj.get("halo_role") != "primitive" or not obj.get("halo_face_camera"):
            continue
        if obj.parent is None:
            continue
        obj.rotation_mode = "QUATERNION"
        obj.rotation_quaternion = obj.parent.matrix_world.to_quaternion().inverted() @ world_quaternion


def update_face_camera(scene):
    """Keep face_camera active across viewport and animation frame updates."""

    if bpy is None or scene is None:
        return
    cached_view = _SCENE_VIEW_QUATERNIONS.get(_scene_key(scene))
    if cached_view is not None:
        _apply_face_camera_orientation(scene, cached_view)
        return
    camera = scene.camera
    if camera is not None:
        _apply_face_camera_orientation(scene, camera.matrix_world.to_quaternion())


def _draw_face_camera():
    """Use the active 3D View orientation for face_camera previews."""

    if bpy is None:
        return
    region_data = getattr(bpy.context, "region_data", None)
    if region_data is None:
        return
    view_quaternion = region_data.view_matrix.inverted().to_quaternion()
    scene = getattr(bpy.context, "scene", None)
    _apply_face_camera_orientation(scene, view_quaternion, remember_view=True)


def update_animation(scene):
    """Evaluate all managed objects for the current scene frame."""

    if bpy is None or getattr(scene, "halo_project", None) is None:
        return
    project = scene.halo_project
    fps = max(1.0, float(scene.render.fps or project.preview_fps or 20.0))
    time_seconds = max(0.0, (float(scene.frame_current) - float(scene.frame_start)) / fps)
    mode = project.preview_mode
    for root in bpy.data.objects:
        if root.get("halo_role") != "definition_root":
            continue
        raw = _definition_raw(scene, root)
        if _core_scene_animation(scene, root, raw, time_seconds, mode):
            continue
        # Definition-level animation is represented on the root Empty.
        root_eval = _apply_object_animation(scene, root, root, project.preview_phase if mode != "IDLE" else time_seconds, mode, time_seconds, None)
        transition_root = _transition_for_node(raw, root, root, mode, time_seconds, project)
        if transition_root is not None:
            _apply_object_animation(scene, root, root, project.preview_phase, mode, time_seconds, transition_root)
        inherited_alpha = 1.0
        inherited_glow = 1.0
        stack = [(child, inherited_alpha, inherited_glow) for child in root.children if child.get("halo_role") == "group"]
        while stack:
            obj, parent_alpha, parent_glow = stack.pop()
            value = _apply_object_animation(scene, root, obj, project.preview_phase if mode != "IDLE" else time_seconds, mode, time_seconds, _transition_for_node(raw, root, obj, mode, time_seconds, project))
            group_raw = _load_json(obj.get("halo_raw_json", "{}"))
            own_alpha = value["alpha"]
            own_glow = value["glow"] * (1.0 if group_raw.get("glowing", True) else float(project.neutral_environment))
            effective_alpha = own_alpha * parent_alpha if group_raw.get("inherit_alpha", True) else own_alpha
            effective_glow = own_glow * parent_glow if group_raw.get("inherit_glow", True) else own_glow
            obj["halo_preview_alpha"] = effective_alpha
            obj["halo_preview_glow"] = effective_glow
            for child in obj.children:
                if child.get("halo_role") == "primitive":
                    child_value = value
                    child["halo_preview_alpha"] = effective_alpha
                    child["halo_preview_glow"] = effective_glow
                    for material in getattr(child.data, "materials", ()):
                        set_material_visual(material, effective_alpha, effective_glow)
                    _apply_mesh_mask_animation(
                        child,
                        project.preview_phase if mode != "IDLE" else time_seconds,
                    )
                elif child.get("halo_role") == "group":
                    stack.append((child, effective_alpha, effective_glow))
    update_face_camera(scene)


def preview_duration(scene, mode: str) -> float:
    """Use authored transition queues rather than the generic UI fallback."""
    project = scene.halo_project
    if mode == "IDLE":
        return 5.0
    maximum = 0.0
    for root in scene.objects:
        if root.get("halo_role") != "definition_root" or root.hide_viewport:
            continue
        try:
            definition = _core_parse_definition(
                _definition_raw(scene, root),
                source_path=root.get("halo_source_path", ""),
                namespace=str(root.get("halo_definition_id", "minecraft:halo")).split(":", 1)[0],
            )
            startup = float(getattr(getattr(definition, "startup", None), "max_duration", 0.0) or 0.0)
            shutdown_config = getattr(definition, "shutdown", None)
            shutdown = float(getattr(shutdown_config, "max_duration", 0.0) or 0.0)
            if shutdown <= 0.0:
                shutdown = startup  # The mod reverses startup when shutdown is absent.
            if mode == "STARTUP":
                maximum = max(maximum, startup)
            elif mode == "SHUTDOWN":
                maximum = max(maximum, shutdown)
            else:
                maximum = max(maximum, startup + max(0.0, float(project.transition_duration)) + shutdown)
        except Exception:
            continue
    if maximum > 0.0:
        return maximum
    return max(0.01, float(project.transition_duration))


def prepare_preview_playback(scene, mode: str) -> float:
    """Reset the timeline and evaluate the first frame before playback starts."""

    project = scene.halo_project
    project.preview_mode = mode
    fps = max(1, int(project.preview_fps))
    scene.render.fps = fps
    scene.frame_start = 1
    duration = preview_duration(scene, mode)
    scene.frame_end = max(2, 1 + int(fps * duration + 1e-6))
    scene.frame_set(scene.frame_start)
    update_animation(scene)
    return duration


if bpy is not None:

    @persistent
    def halo_frame_change(scene, depsgraph=None):
        try:
            update_animation(scene)
        except Exception as exc:
            # Never break Blender's timeline because an individual malformed
            # definition has an unsupported animation term.
            scene.halo_project["halo_last_handler_error"] = str(exc)


    @persistent
    def halo_load_post(_dummy):
        # A cached viewport quaternion belongs to the previous screen/file
        # state.  The first draw in the newly loaded file will establish the
        # current view; until then update_face_camera may use scene.camera.
        _SCENE_VIEW_QUATERNIONS.clear()
        for scene in bpy.data.scenes:
            try:
                from .blender_scene import enforce_managed_transform_locks, update_preview_roots
                from .animation_text import apply_pending_animation_texts
                from .resource_store import ensure_resources
                from .materials import refresh_halo_material_settings
                ensure_resources(scene, restore_saved=True)
                apply_pending_animation_texts(scene, refresh=False)
                enforce_managed_transform_locks(scene, restore=True)
                refresh_halo_material_settings()
                # Rebuild the non-animated anchor pose before evaluating files
                # saved by an older build that has no cached base transform.
                update_preview_roots(scene)
                update_animation(scene)
            except Exception as exc:
                scene.halo_project["halo_resource_warnings"] = json.dumps([f"资源恢复失败：{exc}"], ensure_ascii=False)
        from .resource_store import cleanup_missing_temp_images
        cleanup_missing_temp_images()


    @persistent
    def halo_save_pre(_dummy):
        from .animation_text import apply_pending_animation_texts
        from .resource_store import embed_resources
        for scene in bpy.data.scenes:
            try:
                animation_result = apply_pending_animation_texts(scene, refresh=False)
                for warning in animation_result["errors"]:
                    print("Halo 动画 JSON 保存警告:", warning)
                report = embed_resources(scene)
                for warning in report["warnings"]:
                    print("Halo 资源保存警告:", warning)
            except Exception as exc:
                scene.halo_project["halo_resource_warnings"] = json.dumps([f"资源内嵌失败：{exc}"], ensure_ascii=False)
                print("Halo 资源内嵌失败:", exc)
        from .resource_store import cleanup_missing_temp_images
        cleanup_missing_temp_images()


    @persistent
    def halo_depsgraph_update(scene, depsgraph):
        """Track Outliner selection; static transforms are panel-owned."""

        active = getattr(getattr(bpy.context, "view_layer", None), "objects", None)
        active = getattr(active, "active", None)
        if active is not None and active.get("halo_definition_id") and getattr(scene, "halo_project", None) is not None:
            scene.halo_project.active_definition = active.get("halo_definition_id")
            scene.halo_project.active_uuid = active.get("halo_uuid", "")


    HANDLER_FUNCTIONS = (halo_frame_change, halo_load_post, halo_save_pre, halo_depsgraph_update)
else:  # pragma: no cover
    HANDLER_FUNCTIONS = ()


def register_handlers():
    global _VIEW_DRAW_HANDLE
    if bpy is None:
        return
    if halo_frame_change not in bpy.app.handlers.frame_change_post:
        bpy.app.handlers.frame_change_post.append(halo_frame_change)
    if halo_load_post not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(halo_load_post)
    if halo_save_pre not in bpy.app.handlers.save_pre:
        bpy.app.handlers.save_pre.append(halo_save_pre)
    if halo_depsgraph_update not in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.append(halo_depsgraph_update)
    try:
        scenes = list(bpy.data.scenes)
    except (AttributeError, RuntimeError):
        scenes = ()
    for scene in scenes:
        try:
            from .blender_scene import enforce_managed_transform_locks
            from .resource_store import ensure_resources
            from .materials import refresh_halo_material_settings
            ensure_resources(scene, restore_saved=True)
            enforce_managed_transform_locks(scene, restore=True)
            refresh_halo_material_settings()
        except Exception as exc:
            scene.halo_project["halo_resource_warnings"] = json.dumps([f"资源恢复失败：{exc}"], ensure_ascii=False)
    # Extension installation registers under Blender's _RestrictData wrapper.
    # Defer data access there to the normal load/save recovery hooks.
    if scenes:
        from .resource_store import cleanup_missing_temp_images
        cleanup_missing_temp_images()
    if _VIEW_DRAW_HANDLE is None:
        _VIEW_DRAW_HANDLE = bpy.types.SpaceView3D.draw_handler_add(_draw_face_camera, (), "WINDOW", "POST_VIEW")


def unregister_handlers():
    global _VIEW_DRAW_HANDLE
    if bpy is None:
        return
    for collection in (bpy.app.handlers.frame_change_post, bpy.app.handlers.load_post, bpy.app.handlers.save_pre, bpy.app.handlers.depsgraph_update_post):
        for callback in HANDLER_FUNCTIONS:
            if callback in collection:
                collection.remove(callback)
    if _VIEW_DRAW_HANDLE is not None:
        try:
            bpy.types.SpaceView3D.draw_handler_remove(_VIEW_DRAW_HANDLE, "WINDOW")
        except (ReferenceError, ValueError):
            pass
        _VIEW_DRAW_HANDLE = None
    _SCENE_VIEW_QUATERNIONS.clear()


__all__ = [
    "evaluate_term",
    "evaluate_terms",
    "evaluate_layer_animation",
    "easing_value",
    "evaluate_transition",
    "transition_duration",
    "update_face_camera",
    "update_animation",
    "preview_duration",
    "prepare_preview_playback",
    "register_handlers",
    "unregister_handlers",
]
