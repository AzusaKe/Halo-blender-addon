"""Validation helpers for the Halo pack editor.

Validation is intentionally non-destructive.  The parser accepts unknown
fields and future primitive kinds so users can inspect/repair a pack; this
module turns those conditions, and values the Java parser would reject, into
actionable diagnostics for the UI and export operator.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from math import isfinite
import re
from typing import Any, Iterable, Iterator, Mapping

from .models import (
    AnimationTerm,
    BillboardPrimitive,
    HaloDefinition,
    HaloGroup,
    MeshPrimitive,
    PackProject,
    RawPrimitive,
    ResourceLocation,
    RingPrimitive,
    SchemaVersion,
    TransitionConfig,
    TransitionProperty,
    TransitionSegment,
)


@dataclass(frozen=True)
class ValidationIssue:
    severity: str
    code: str
    message: str
    path: str = ""

    @property
    def level(self) -> str:
        return self.severity

    def __str__(self) -> str:
        location = f" ({self.path})" if self.path else ""
        return f"[{self.severity}] {self.code}{location}: {self.message}"


@dataclass
class ValidationReport:
    issues: list[ValidationIssue] = field(default_factory=list)

    @property
    def errors(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == "warning"]

    @property
    def infos(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == "info"]

    @property
    def ok(self) -> bool:
        return not self.errors

    @property
    def valid(self) -> bool:
        return self.ok

    def add(self, severity: str, code: str, message: str, path: str = "") -> ValidationIssue:
        issue = ValidationIssue(severity.lower(), code, message, path)
        self.issues.append(issue)
        return issue

    def error(self, code: str, message: str, path: str = "") -> ValidationIssue:
        return self.add("error", code, message, path)

    def warning(self, code: str, message: str, path: str = "") -> ValidationIssue:
        return self.add("warning", code, message, path)

    def info(self, code: str, message: str, path: str = "") -> ValidationIssue:
        return self.add("info", code, message, path)

    def extend(self, other: "ValidationReport", prefix: str = "") -> None:
        for issue in other.issues:
            path = f"{prefix}.{issue.path}" if prefix and issue.path else prefix or issue.path
            self.issues.append(ValidationIssue(issue.severity, issue.code, issue.message, path))

    def __iter__(self) -> Iterator[ValidationIssue]:
        return iter(self.issues)

    def __len__(self) -> int:
        return len(self.issues)

    def __bool__(self) -> bool:
        return self.ok

    def summary(self) -> str:
        return f"{len(self.errors)} error(s), {len(self.warnings)} warning(s), {len(self.infos)} info(s)"


ID_RE = re.compile(r"^[a-z0-9_.-]+:[a-z0-9_./-]+$")
KNOWN_FUNCTIONS = {"sin", "cos", "linear"}
KNOWN_EASINGS = {"linear", "ease_out_cubic", "ease_in_out_cubic", "ease-out-cubic", "ease-in-out-cubic"}


def validate_definition(definition: HaloDefinition, *, strict_unknown: bool = False) -> ValidationReport:
    report = ValidationReport()
    if not isinstance(definition.id, str) or not definition.id:
        report.error("missing_id", "definition id must be a non-empty string", "id")
    elif not ID_RE.match(definition.id):
        report.error("invalid_id", "id must use namespace:path (lower-case resource id syntax)", "id")

    if definition.schema_version > SchemaVersion.CURRENT:
        report.warning(
            "future_schema", f"schema {definition.schema_version} is newer than supported {SchemaVersion.CURRENT}", "version"
        )
    if definition.orientation_mode not in {"locked", "free", "sync"}:
        report.error("orientation_mode", "orientation_mode must be locked, free, or sync", "orientation_mode")
    for name, value in zip(("x", "y", "z"), definition.sync_offset):
        _finite(report, value, f"sync_offset.{name}")
    _validate_animation(definition.animation, report, "animation")
    _validate_positioning(definition, report)
    _validate_damping(definition, report)

    seen_objects: set[int] = set()
    seen_ids: dict[str, str] = {}
    for index, group in enumerate(definition.groups):
        _validate_group(group, report, f"layers[{index}]", seen_objects, seen_ids)
    _validate_transitions(definition, report, seen_ids)

    if strict_unknown:
        # Unknown keys are never errors by default; strict mode is useful to
        # an author who wants a pack constrained to the current schema.
        known_root = {
            "id", "version", "orientation_mode", "allow_angular_momentum", "sync_offset", "layers", "shape",
            "animation", "hide_on_sleep", "display_in_invisible", "positioning", "damping", "startup", "shutdown",
        }
        for key in definition.raw:
            if key not in known_root:
                report.warning("unknown_field", f"field {key!r} is retained but not interpreted", key)
    return report


def validate_pack(project: PackProject, *, strict_unknown: bool = False) -> ValidationReport:
    report = ValidationReport()
    parse_errors = getattr(project, "parse_errors", [])
    for path, message in parse_errors:
        report.error("definition_parse", message, path)
    seen: dict[str, int] = {}
    for index, definition in enumerate(project.definitions):
        if definition.id in seen:
            report.error("duplicate_definition", f"duplicate definition id (already at index {seen[definition.id]})", f"definitions[{index}].id")
        else:
            seen[definition.id] = index
        report.extend(validate_definition(definition, strict_unknown=strict_unknown), f"definitions[{index}]")
    if "pack.mcmeta" not in project.files and not project.pack_meta:
        report.warning("missing_pack_meta", "pack.mcmeta is missing; a new pack will receive default metadata", "pack.mcmeta")
    elif project.files.get("pack.mcmeta"):
        # A malformed metadata file is represented by an empty parsed map but
        # can still be intentionally edited in the project UI.
        if not project.pack_meta:
            report.error("invalid_pack_meta", "pack.mcmeta is not a JSON object", "pack.mcmeta")
    return report


def assert_valid(value: HaloDefinition | PackProject, *, strict_unknown: bool = False) -> ValidationReport:
    report = validate_definition(value, strict_unknown=strict_unknown) if isinstance(value, HaloDefinition) else validate_pack(value, strict_unknown=strict_unknown)
    if not report.ok:
        raise ValueError("; ".join(str(issue) for issue in report.errors))
    return report


def _validate_group(
    group: HaloGroup,
    report: ValidationReport,
    path: str,
    seen_objects: set[int],
    seen_ids: dict[str, str],
) -> None:
    identity = id(group)
    if identity in seen_objects:
        report.error("group_cycle", "group object is repeated in its own tree", path)
        return
    seen_objects.add(identity)
    if group.id:
        if group.id in seen_ids:
            report.warning("duplicate_group_id", f"group id duplicates {seen_ids[group.id]}; id_overrides will target both groups", f"{path}.id")
        else:
            seen_ids[group.id] = path
    for component, value in zip(("x", "y", "z"), group.position):
        _finite(report, value, f"{path}.position.{component}")
    for component, value in zip(("yaw", "pitch", "roll"), group.rotation):
        _finite(report, value, f"{path}.rotation.{component}")
    _finite(report, group.scale, f"{path}.scale")
    if group.scale <= 0:
        report.error("invalid_scale", "group scale must be greater than zero", f"{path}.scale")
    _validate_animation(group.animation, report, f"{path}.animation")
    for index, primitive in enumerate(group.primitives):
        _validate_primitive(primitive, report, f"{path}.primitives[{index}]")
    for index, child in enumerate(group.children):
        _validate_group(child, report, f"{path}.children[{index}]", seen_objects, seen_ids)


def _validate_primitive(primitive: Any, report: ValidationReport, path: str) -> None:
    if isinstance(primitive, BillboardPrimitive):
        _validate_texture(primitive.texture, report, f"{path}.texture")
        _validate_size(primitive.size, report, f"{path}.size")
    elif isinstance(primitive, RingPrimitive):
        _validate_texture(primitive.outer_texture, report, f"{path}.texture")
        if primitive.inner_texture is not None:
            _validate_texture(primitive.inner_texture, report, f"{path}.inner_texture")
        _validate_size(primitive.size, report, f"{path}.size")
        if primitive.segments < 3:
            report.error("ring_segments", "ring segments must be at least 3", f"{path}.segments")
    elif isinstance(primitive, MeshPrimitive):
        _validate_texture(primitive.model, report, f"{path}.model")
        if primitive.model and not primitive.model.endswith(".obj"):
            report.error("mesh_model", "mesh model must reference an .obj resource", f"{path}.model")
        _validate_texture(primitive.texture, report, f"{path}.texture")
        authored_preserve = primitive.raw.get("preserve_proportions") if isinstance(primitive.raw, Mapping) else None
        if isinstance(primitive.raw, Mapping) and "preserve_proportions" in primitive.raw and not isinstance(authored_preserve, bool):
            report.error("mesh_preserve_proportions", "mesh preserve_proportions must be a boolean", f"{path}.preserve_proportions")
        authored_scale = primitive.raw.get("scale") if isinstance(primitive.raw, Mapping) else None
        if isinstance(primitive.raw, Mapping) and "scale" in primitive.raw:
            if isinstance(authored_scale, bool) or not isinstance(authored_scale, (int, float)):
                report.error("mesh_scale", "mesh scale must be a number", f"{path}.scale")
            elif not isfinite(float(authored_scale)) or float(authored_scale) < 0:
                report.error("mesh_scale", "mesh scale must be finite and nonnegative", f"{path}.scale")
        if not isfinite(float(primitive.scale)) or primitive.scale < 0:
            report.error("mesh_scale", "mesh scale must be finite and nonnegative", f"{path}.scale")
        authored_size = primitive.raw.get("size") if isinstance(primitive.raw, Mapping) else None
        if authored_size is None and not primitive.preserve_proportions:
            report.error("mesh_size_required", "mesh size is required unless preserve_proportions is true", f"{path}.size")
        elif authored_size is not None and (not isinstance(authored_size, (list, tuple)) or len(authored_size) != 3):
            report.error("mesh_size_components", "mesh size must contain exactly 3 components", f"{path}.size")
        if primitive.size is not None:
            _validate_mesh_size(primitive.size, report, f"{path}.size")
        authored_material = primitive.raw.get("material") if isinstance(primitive.raw, Mapping) else None
        if authored_material is not None and not isinstance(authored_material, Mapping):
            report.error("mesh_material", "mesh material must be an object", f"{path}.material")
        effects = authored_material.get("effects") if isinstance(authored_material, Mapping) else None
        if effects is not None and not isinstance(effects, list):
            report.error("mesh_effects", "mesh material effects must be an array", f"{path}.material.effects")
        elif isinstance(effects, list):
            if len(effects) > 1:
                report.error("mesh_effect_count", "mesh material supports at most one alpha_mask effect", f"{path}.material.effects")
            for index, effect in enumerate(effects):
                if not isinstance(effect, Mapping) or str(effect.get("type", "")) != "alpha_mask":
                    report.error("mesh_effect_type", "mesh material only supports alpha_mask", f"{path}.material.effects[{index}]")
        mask = primitive.material.alpha_mask
        if mask is not None:
            _validate_texture(mask.texture, report, f"{path}.material.effects[0].texture")
            if mask.mode not in {"linear", "step"}:
                report.error("mesh_mask_mode", "alpha_mask mode must be linear or step", f"{path}.material.effects[0].mode")
            if not isfinite(mask.threshold) or not 0.0 <= mask.threshold <= 1.0:
                report.error("mesh_mask_threshold", "alpha_mask threshold must be within [0,1]", f"{path}.material.effects[0].threshold")
            for axis, terms in (("u", mask.offset_u), ("v", mask.offset_v)):
                for index, term in enumerate(terms):
                    term_path = f"{path}.material.effects[0].uv_offset.{axis}[{index}]"
                    if term.function.lower() not in KNOWN_FUNCTIONS:
                        report.error("animation_function", f"unknown animation function {term.function!r}", term_path)
                    for field in ("A", "omega", "phi", "start", "speed"):
                        _finite(report, getattr(term, field), f"{term_path}.{field}")
    elif isinstance(primitive, RawPrimitive):
        report.warning("unknown_primitive", f"primitive type {primitive.type!r} is not interpreted and will be retained", path)
    else:
        report.error("primitive_type", "object is not a supported Halo primitive", path)


def _validate_animation(animation: Any, report: ValidationReport, path: str) -> None:
    if animation is None:
        return
    for channel in animation.CHANNELS:
        for index, term in enumerate(getattr(animation, channel)):
            term_path = f"{path}.{channel}[{index}]"
            if term.function.lower() not in KNOWN_FUNCTIONS:
                report.error("animation_function", f"unknown animation function {term.function!r}", term_path)
            for field in ("A", "omega", "phi", "start", "speed"):
                _finite(report, getattr(term, field), f"{term_path}.{field}")


def _validate_transitions(definition: HaloDefinition, report: ValidationReport, seen_ids: Mapping[str, str]) -> None:
    _validate_transition_config(definition.startup, report, "startup", True, seen_ids)
    _validate_transition_config(definition.shutdown, report, "shutdown", False, seen_ids)


def _validate_transition_config(
    config: TransitionConfig | None,
    report: ValidationReport,
    path: str,
    startup: bool,
    seen_ids: Mapping[str, str],
) -> None:
    if config is None:
        return
    for group_id in config.id_overrides:
        if group_id not in seen_ids:
            report.warning("unknown_override_group", f"id_overrides entry {group_id!r} does not match a group id", f"{path}.id_overrides.{group_id}")
    _validate_segment_list(config.segments, report, f"{path}.segments", startup)
    for group_id, segments in config.id_overrides.items():
        _validate_segment_list(segments, report, f"{path}.id_overrides.{group_id}.segments", startup)


def _validate_segment_list(segments: Iterable[TransitionSegment], report: ValidationReport, path: str, startup: bool) -> None:
    segments = list(segments)
    for index, segment in enumerate(segments):
        segment_path = f"{path}[{index}]"
        if not isfinite(segment.duration) or segment.duration <= 0:
            report.error("transition_duration", "segment duration must be finite and greater than zero", f"{segment_path}.duration")
        easing = segment.easing.lower().replace("-", "_")
        if easing not in {e.replace("-", "_") for e in KNOWN_EASINGS}:
            report.error("transition_easing", f"unknown easing {segment.easing!r}", f"{segment_path}.easing")
        for name in ("offset", "scale", "alpha", "rotation"):
            prop = getattr(segment, name)
            if prop is None:
                continue
            expected = 1 if name == "alpha" else 3
            if prop.from_value is not None and len(prop.from_value) != expected:
                report.error("transition_components", f"{name}.from must have {expected} component(s)", f"{segment_path}.{name}.from")
            if prop.to_value is not None and len(prop.to_value) != expected:
                report.error("transition_components", f"{name}.to must have {expected} component(s)", f"{segment_path}.{name}.to")
            if prop.property_duration is not None and (not isfinite(prop.property_duration) or prop.property_duration <= 0):
                report.error("property_duration", "property duration must be finite and greater than zero", f"{segment_path}.{name}.duration")
            if prop.property_easing is not None and prop.property_easing.lower().replace("-", "_") not in {e.replace("-", "_") for e in KNOWN_EASINGS}:
                report.error("property_easing", f"unknown property easing {prop.property_easing!r}", f"{segment_path}.{name}.easing")
            if name != "rotation" and prop.degrees is not None:
                report.warning("degrees_ignored", "degrees is only meaningful for rotation and will be ignored", f"{segment_path}.{name}.degrees")
            for endpoint, values in (("from", prop.from_value), ("to", prop.to_value), ("degrees", prop.degrees)):
                if values is not None:
                    for axis, value in enumerate(values):
                        _finite(report, value, f"{segment_path}.{name}.{endpoint}[{axis}]")
    # Boundary diagnostics match the parser's documented convention.
    for name in ("offset", "scale", "alpha", "rotation"):
        active = [(i, getattr(s, name)) for i, s in enumerate(segments) if getattr(s, name) is not None]
        if not active:
            continue
        index, prop = active[-1 if not startup else 0]
        required = prop.from_value if startup else prop.to_value
        if required is None:
            report.warning(
                "transition_boundary",
                f"{name}.{ 'from' if startup else 'to' } is missing on the {'first' if startup else 'last'} active segment; steady state will be used",
                f"{path}[{index}].{name}",
            )


def _validate_positioning(definition: HaloDefinition, report: ValidationReport) -> None:
    for axis, value in zip(("x", "y", "z"), definition.positioning.offset):
        _finite(report, value, f"positioning.offset.{axis}")
    _finite(report, definition.positioning.scale, "positioning.scale")
    if definition.positioning.scale <= 0:
        report.error("invalid_positioning_scale", "positioning scale must be greater than zero", "positioning.scale")


def _validate_damping(definition: HaloDefinition, report: ValidationReport) -> None:
    damping = definition.damping
    for name in ("linear_factor", "angular_factor", "max_linear_distance", "max_angular_degrees", "angular_momentum_factor", "max_angular_momentum_degrees"):
        _finite(report, getattr(damping, name), f"damping.{name}")
    for name in ("linear_factor", "angular_factor", "angular_momentum_factor"):
        value = getattr(damping, name)
        if not 0.0 <= value <= 1.0:
            report.warning("damping_range", f"{name} is outside the usual [0, 1] range", f"damping.{name}")
    for name in ("max_linear_distance", "max_angular_degrees", "max_angular_momentum_degrees"):
        if getattr(damping, name) < 0:
            report.error("damping_negative", f"{name} cannot be negative", f"damping.{name}")


def _validate_size(size: Iterable[float], report: ValidationReport, path: str) -> None:
    values = list(size)
    if len(values) != 2:
        report.error("size_components", "size must contain exactly two values", path)
        return
    for i, value in enumerate(values):
        _finite(report, value, f"{path}[{i}]")
        if value <= 0:
            report.error("size_positive", "size values must be greater than zero", f"{path}[{i}]")


def _validate_mesh_size(size: Iterable[float], report: ValidationReport, path: str) -> None:
    values = list(size)
    if len(values) != 3:
        report.error("size_components", "mesh size must contain exactly three values", path)
        return
    for i, value in enumerate(values):
        _finite(report, value, f"{path}[{i}]")
        if value < 0:
            report.error("size_nonnegative", "mesh size values must be nonnegative", f"{path}[{i}]")


def _validate_texture(texture: str, report: ValidationReport, path: str) -> None:
    if not isinstance(texture, str) or not texture:
        report.error("missing_texture", "primitive texture is required", path)
        return
    try:
        location = ResourceLocation.parse(texture)
    except ValueError:
        report.error("invalid_texture", "texture must use namespace:path syntax", path)
        return
    if any(part in ("", ".", "..") for part in location.path.replace("\\", "/").split("/")):
        report.error("unsafe_texture_path", "texture path contains an empty or parent component", path)


def has_integral_texture_scale(first_width: int, first_height: int, second_width: int, second_height: int) -> bool:
    """Match HaloCore's alpha-mask resolution compatibility rule exactly."""

    dimensions = (first_width, first_height, second_width, second_height)
    if any(isinstance(value, bool) or not isinstance(value, int) or value <= 0 for value in dimensions):
        return False

    def is_integer_enlargement(wide_w: int, wide_h: int, small_w: int, small_h: int) -> bool:
        return wide_w % small_w == 0 and wide_h % small_h == 0 \
            and wide_w // small_w == wide_h // small_h

    return is_integer_enlargement(first_width, first_height, second_width, second_height) \
        or is_integer_enlargement(second_width, second_height, first_width, first_height)


def _finite(report: ValidationReport, value: Any, path: str) -> None:
    try:
        if not isfinite(float(value)):
            report.error("not_finite", "value must be finite", path)
    except (TypeError, ValueError):
        report.error("not_number", "value must be numeric", path)


__all__ = [
    "ValidationIssue", "ValidationReport", "validate_definition", "validate_pack", "assert_valid",
    "has_integral_texture_scale",
]
